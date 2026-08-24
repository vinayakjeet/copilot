"""The status number and every headline figure, one command.

    uv run python bench/status.py

Reads the artifacts the other scripts wrote (eval/results.json,
safety/results.json) and adds what can be measured cheaply right now: the
cancellation proof against the live warehouse and the warehouse's own shape.
A number that no script here regenerates does not belong in the README; most
of them come from this one.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

ROOT = Path(__file__).resolve().parent.parent


def load(path: Path) -> dict | None:
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    return None


async def cancellation_proof(ro_url: str) -> dict:
    """Fire pg_cancel_backend at a genuinely slow query and time the stop."""
    from copilot.analyst import Analyst

    analyst = Analyst(ro_url, statement_timeout_ms=60_000, row_cap=100)
    await analyst.warm()
    slow_sql = (
        "SELECT count(*) FROM generate_series(1, 300000) a, "
        "generate_series(1, 300000) b"
    )
    import time

    task = asyncio.create_task(analyst.execute("status-proof", slow_sql))
    for _ in range(300):
        if analyst.known_pid("status-proof"):
            break
        await asyncio.sleep(0.01)
    pid = analyst.known_pid("status-proof")
    # A cancel issued before the statement starts executing lands on nothing,
    # which previously masqueraded as a fast stop until the statement timeout
    # fired a minute later. So: keep signalling until the task actually dies,
    # and say plainly which mechanism ended it.

    started = time.monotonic()
    deadline = 15.0
    outcome = "unclear"
    while True:
        if task.done():
            outcome = f"stopped {analyst.known_pid('status-proof') is None}"
            break
        if time.monotonic() - started > deadline:
            outcome = "cancel did not land within 15s"
            break
        await analyst.cancel("status-proof")
        await asyncio.sleep(0.4)
    if not task.done():
        try:
            await asyncio.wait_for(asyncio.shield(task), timeout=70)
            outcome = "ran to completion"
        except Exception:  # noqa: BLE001 - cancelled or timed out, both fine
            pass
    stop_ms = round((time.monotonic() - started) * 1000, 1)
    with contextlib.suppress(Exception):
        await task

    # The natural duration of this query is minutes; measure a bounded proxy so
    # the saved-time claim has a number attached even on fast machines.
    bounded_sql = (
        "SELECT count(*) FROM generate_series(1, 3000) a, generate_series(1, 1000) b"
    )
    t0 = time.monotonic()
    try:
        await analyst.execute("status-baseline", bounded_sql)
        baseline_ms = round((time.monotonic() - t0) * 1000, 1)
    except Exception:  # noqa: BLE001 - timing only
        baseline_ms = None
    await analyst.close()
    return {
        "backend_pid": pid,
        "stop_ms": stop_ms,
        "outcome": outcome,
        "smaller_bomb_ms": baseline_ms,
    }


async def warehouse_shape(ro_url: str) -> dict:
    import psycopg

    conn = await psycopg.AsyncConnection.connect(ro_url)
    try:
        cur = conn.cursor()
        await cur.execute(
            "SELECT count(*) FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
            "WHERE n.nspname = 'warehouse' AND c.relkind IN ('r','p','v','m')"
        )
        relations = (await cur.fetchone())[0]
        await cur.execute(
            "SELECT count(*) FROM pg_attribute a "
            "JOIN pg_class c ON c.oid = a.attrelid "
            "JOIN pg_namespace n ON n.oid = c.relnamespace "
            "WHERE n.nspname = 'warehouse' AND a.attnum > 0 AND NOT a.attisdropped "
            "AND c.relkind IN ('r','p')"
        )
        columns = (await cur.fetchone())[0]
        await cur.execute("SELECT count(*) FROM warehouse.orders")
        orders = (await cur.fetchone())[0]
        return {"relations": relations, "columns": columns, "orders_rows": orders}
    finally:
        await conn.close()


async def main_async() -> int:
    from dotenv import load_dotenv

    load_dotenv(override=True)
    import os

    ro_url = os.environ.get("COPILOT_RO_URL", "")

    print("Copilot status")
    print("=" * 64)

    safety = load(ROOT / "safety" / "results.json")
    if safety:
        rows = safety["rows"]
        total = len([r for r in rows if r["id"] != "s18"])
        stopped = len(
            [
                r
                for r in rows
                if r["id"] != "s18"
                and (
                    "caught" in (r["naive"], r["static"])
                    or str(r.get("runtime", "")).startswith("blocked")
                )
            ]
        )
        naive_only = len([r for r in rows if r["id"] != "s18" and r["naive"] == "caught"])
        sneaky = next(r for r in rows if r["id"] == "s06")
        inj = next((r for r in rows if r["id"] == "s18"), {})
        print(f"safety   : {stopped}/{total} attack cases stopped by the full stack "
              f"({naive_only}/{total} by the naive allowlist alone)")
        print(f"           mutating CTE: naive={sneaky['naive']}, static={sneaky['static']}, "
              f"runtime={sneaky['runtime']}")
        if inj:
            print(f"           poisoned-comment injection: tagging {inj.get('tagged')} "
                  f"(guard {inj.get('tagged_guard')}), untagged {inj.get('untagged')} "
                  f"(guard {inj.get('untagged_guard')})")
    else:
        print("safety   : not yet measured (run python -m safety.run_safety_suite)")

    evalr = load(ROOT / "eval" / "results.json")
    if evalr:
        print(
            f"eval     : execution accuracy {evalr['accuracy_mean']:.3f} "
            f"+- {evalr['accuracy_stdev']:.3f} over {evalr['runs']} runs x "
            f"{evalr['n_questions']} questions"
        )
        print(f"           model {evalr['model']}, measured {evalr['measured_at']}")
        print(
            f"           raw labels (4 relabelled questions): "
            f"{evalr['raw_label_accuracy']:.3f} vs "
            f"corrected {evalr['corrected_label_accuracy']:.3f}"
        )
    else:
        print("eval     : not yet measured (run python -m eval.run_eval)")

    if ro_url:
        shape = await warehouse_shape(ro_url)
        print(f"warehouse: {shape['relations']} relations, {shape['columns']} columns, "
              f"{shape['orders_rows']} orders")
        proof = await cancellation_proof(ro_url)
        twin = (
            f"{proof['smaller_bomb_ms']} ms"
            if proof.get("smaller_bomb_ms") is not None
            else "n/a"
        )
        print(f"cancel   : pg_cancel_backend against pid {proof['backend_pid']}: "
              f"{proof['stop_ms']} ms to take effect ({proof['outcome']}; "
              f"bounded twin query: {twin})")
    else:
        print("warehouse: COPILOT_RO_URL not set; live checks skipped")

    return 0


def main() -> int:
    import asyncio
    import sys as _sys

    if _sys.platform == "win32":
        policy = getattr(asyncio, "WindowsSelectorEventLoopPolicy", None)
        if policy is not None:
            asyncio.set_event_loop_policy(policy())
    return asyncio.run(main_async())


if __name__ == "__main__":
    raise SystemExit(main())
