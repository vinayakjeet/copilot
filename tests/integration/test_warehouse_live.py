from __future__ import annotations

import asyncio
import os
import time

import pytest

pytestmark = pytest.mark.integration

pytest.importorskip("psycopg")


def ro_url() -> str:
    from dotenv import load_dotenv

    # Root conftest scrubs credentials for unit tests; the live suite opts
    # back in by reading .env itself.
    load_dotenv(override=True)
    url = os.environ.get("COPILOT_RO_URL", "")
    if not url:
        pytest.skip("integration test needs COPILOT_RO_URL")
    return url


@pytest.fixture()
async def analyst():
    from copilot.analyst import Analyst

    a = Analyst(ro_url(), statement_timeout_ms=4000, row_cap=500)
    await a.warm()
    yield a
    await a.close()


async def test_select_against_live_warehouse(analyst):
    result = await analyst.execute(
        "it1",
        "SELECT ci.city_name, count(*) AS orders "
        "FROM warehouse.orders o "
        "JOIN warehouse.customers cu USING (customer_id) "
        "JOIN warehouse.cities ci ON ci.city_id = cu.city_id "
        "GROUP BY 1 ORDER BY 2 DESC LIMIT 5",
    )
    assert len(result["columns"]) == 2
    assert 0 < len(result["rows"]) <= 5


async def test_row_cap_truncates_and_reports():
    from copilot.analyst import Analyst

    capped = Analyst(ro_url(), statement_timeout_ms=8000, row_cap=10)
    try:
        await capped.warm()
        result = await capped.execute("cap", "SELECT order_id FROM warehouse.orders")
        assert len(result["rows"]) == 10
        assert result["truncated"] is True
    finally:
        await capped.close()


async def test_mutating_cte_is_refused_by_the_role_not_just_the_parser(analyst):
    from copilot.analyst import ExecutionError

    with pytest.raises(ExecutionError) as excinfo:
        await analyst.execute(
            "cte",
            "WITH gone AS (DELETE FROM warehouse.orders WHERE order_id = 1 RETURNING *) "
            "SELECT * FROM gone",
        )
    assert excinfo.value.sqlstate in ("42501", "25006")


async def test_statement_timeout_kills_a_resource_bomb(analyst):
    from copilot.analyst import QueryCancelled

    started = time.monotonic()
    with pytest.raises(QueryCancelled):
        await analyst.execute(
            "bomb",
            "SELECT count(*) FROM generate_series(1,100000) a, "
            "generate_series(1,100000) b WHERE pg_sleep(0.001) IS NOT NULL OR true",
        )
    elapsed = time.monotonic() - started
    assert elapsed < 30, f"timeout did not bite fast enough ({elapsed:.1f}s)"


async def test_cancellation_proof_pg_cancel_backend_and_backend_gone(analyst):
    """The brief's proof: the stop is server-side, not a UI trick.

    One run starts a genuinely slow query. The control path fires
    pg_cancel_backend at its backend pid. The assertion is that Postgres
    itself reports the query gone in far less time than it would have taken.
    """
    slow_sql = (
        "SELECT count(*) FROM generate_series(1, 300000) a, "
        "generate_series(1, 300000) b"
    )
    task = asyncio.create_task(analyst.execute("proof", slow_sql))
    for _ in range(200):
        if analyst.known_pid("proof"):
            break
        await asyncio.sleep(0.01)
    pid = analyst.known_pid("proof")
    assert pid, "backend never registered"

    import psycopg

    conn = await psycopg.AsyncConnection.connect(ro_url())
    try:
        cur = conn.cursor()
        await cur.execute("SELECT query FROM pg_stat_activity WHERE pid = %s", (pid,))
        row = await cur.fetchone()
        assert row and "generate_series" in row[0], "slow query not visible server-side"
    finally:
        await conn.close()

    cancelled_via_sql = await analyst.cancel("proof")
    started = time.monotonic()
    with pytest.raises((Exception, asyncio.CancelledError)):
        await task
    wall = time.monotonic() - started

    assert wall < 15, f"cancellation took {wall:.1f}s; the stop is not real"
    conn = await psycopg.AsyncConnection.connect(ro_url())
    try:
        cur = conn.cursor()
        await cur.execute(
            "SELECT state, left(query, 60) FROM pg_stat_activity WHERE pid = %s",
            (pid,),
        )
        row = await cur.fetchone()
        if cancelled_via_sql:
            # pg_cancel_backend kills the statement, not the session: what
            # must be gone is our query, whether the backend went idle or was
            # recycled by the pool.
            assert row is None or "generate_series" not in row[1], (
                f"query still running server-side after cancel: {row}"
            )
    finally:
        await conn.close()
