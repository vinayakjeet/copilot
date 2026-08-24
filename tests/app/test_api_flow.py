from __future__ import annotations

import json

from fastapi.testclient import TestClient
from httpx import Response

from app.main import create_app
from copilot.analyst import ExecutionError
from tests.conftest import FakeAnalyst, ScriptedProvider

GOOD_SQL = "SELECT count(*) FROM warehouse.orders"


def make_client(monkeypatch, provider: ScriptedProvider, analyst: FakeAnalyst):
    from llm import ChatClient

    app = create_app()
    app.state.llm_client = ChatClient(provider)
    app.state.analyst = analyst
    for stage in ("boot", "warehouse", "schema"):
        app.state.readiness.begin(stage)
        app.state.readiness.complete(stage)

    # The catalog the linking step reads; small and deterministic.
    from copilot.schema_linking import ColumnInfo, TableInfo

    app.state.catalog = {
        "orders": TableInfo(
            "orders",
            "table",
            (ColumnInfo("order_id", "bigint"), ColumnInfo("grand_total_inr", "numeric")),
        )
    }
    return TestClient(app)


def parse_sse(response: Response) -> list[tuple[str, dict]]:
    events: list[tuple[str, dict]] = []
    for chunk in response.text.split("\n\n"):
        if not chunk.strip():
            continue
        event, data = "message", ""
        for line in chunk.split("\n"):
            if line.startswith("event: "):
                event = line[7:].strip()
            elif line.startswith("data: "):
                data += line[6:]
        if data:
            events.append((event, json.loads(data)))
    return events


def test_full_happy_path_query_preview_approve_rows(monkeypatch):
    provider = ScriptedProvider(f"```sql\n{GOOD_SQL}\n```")
    analyst = FakeAnalyst()
    client = make_client(monkeypatch, provider, analyst)

    r = client.post("/api/query", json={"question": "how many orders do we have"})
    assert r.status_code == 200
    events = parse_sse(r)
    [name for name, _ in events]

    meta = events[0][1]
    assert set(meta["tables"]) == {"orders"}

    preview = next(payload for name, payload in events if name == "preview")
    assert preview["sql"] == GOOD_SQL
    assert preview["blocked"] is False

    run_id = meta["run_id"]
    assert client.app.state.registry.get(run_id).phase == "awaiting_approval"

    r2 = client.post("/api/approve", json={"run_id": run_id})
    names = [n for n, _ in parse_sse(r2)]
    assert names[:2] == ["columns", "rows"]
    assert "chart" in names and "stats" in names
    assert analyst.executed == [GOOD_SQL]
    assert client.app.state.registry.get(run_id).phase == "done"


def test_reject_never_executes(monkeypatch):
    provider = ScriptedProvider(GOOD_SQL)
    analyst = FakeAnalyst()
    client = make_client(monkeypatch, provider, analyst)

    events = parse_sse(client.post("/api/query", json={"question": "count the orders"}))
    run_id = events[0][1]["run_id"]
    assert client.post("/api/reject", json={"run_id": run_id}).status_code == 200
    assert client.post("/api/approve", json={"run_id": run_id}).status_code == 409
    assert analyst.executed == []


def test_stop_during_generation_returns_stopped_event(monkeypatch):
    provider = ScriptedProvider("SELECT " + "x" * 400, delay=0.01, chunk=3)
    analyst = FakeAnalyst()
    client = make_client(monkeypatch, provider, analyst)

    import threading

    stop_result = {}

    def stopper():
        import time as t

        for _ in range(50):
            t.sleep(0.02)
            runs = list(client.app.state.registry._runs.values())
            if runs:
                stop_result["r"] = client.post("/api/stop", json={"run_id": runs[0].id}).json()
                return

    thread = threading.Thread(target=stopper)
    thread.start()
    events = parse_sse(client.post("/api/query", json={"question": "long question please"}))
    thread.join(timeout=5)
    assert any(name == "stopped" for name, _ in events)
    assert stop_result.get("r", {}).get("stopped") is True


def test_execution_failure_triggers_correction_and_new_gate(monkeypatch):
    provider = ScriptedProvider(
        GOOD_SQL,
        delay=0.0,
        chunk=64,
    )
    # First reply is the bad statement, second is the correction.
    provider.reply = "SELECT nope FROM missing_table"
    analyst = FakeAnalyst(
        fail=ExecutionError("42P01", 'relation "missing_table" does not exist',
                            "statement could not be executed against the warehouse")
    )
    client = make_client(monkeypatch, provider, analyst)

    events = parse_sse(client.post("/api/query", json={"question": "show me the table"}))
    next(p for n, p in events if n == "preview")
    run_id = events[0][1]["run_id"]

    # Second scripted reply for the correction call.
    provider.reply = GOOD_SQL
    r2 = client.post("/api/approve", json={"run_id": run_id})
    payloads = {n: p for n, p in parse_sse(r2)}
    assert "exec_error" in payloads
    corr = payloads.get("correction_preview")
    assert corr and corr["sql"] == GOOD_SQL
    assert corr["prior_error"].startswith("statement could not be executed")
    # Exactly one execution happened: the statement that was approved. The
    # correction is only a preview and must not run itself.
    assert analyst.executed == ["SELECT nope FROM missing_table"]


def test_blocked_sql_cannot_be_approved_even_if_forced(monkeypatch):
    provider = ScriptedProvider(
        "WITH gone AS (DELETE FROM warehouse.orders RETURNING *) SELECT * FROM gone"
    )
    analyst = FakeAnalyst()
    client = make_client(monkeypatch, provider, analyst)

    events = parse_sse(client.post("/api/query", json={"question": "delete everything"}))
    preview = next(p for n, p in events if n == "preview")
    assert preview["blocked"] is True
    run_id = events[0][1]["run_id"]

    # The API refuses to run it regardless of the client's enthusiasm.
    r = client.post("/api/approve", json={"run_id": run_id})
    assert r.status_code == 422
    assert analyst.executed == []


def test_status_reports_warming_then_ready_shape(monkeypatch):
    provider = ScriptedProvider(GOOD_SQL)
    client = make_client(monkeypatch, provider, FakeAnalyst())
    snap = client.get("/api/status").json()
    assert snap["ready"] is True
    assert snap["warehouse_tables"] == 1
    assert set(snap["stages"]) == {"boot", "warehouse", "schema"}


def test_query_before_ready_is_503(monkeypatch):
    app = create_app()
    from llm import ChatClient
    app.state.llm_client = ChatClient(ScriptedProvider(GOOD_SQL))
    app.state.analyst = FakeAnalyst()
    client = TestClient(app)   # readiness untouched: nothing complete()
    r = client.post("/api/query", json={"question": "anything at all"})
    assert r.status_code == 503


def test_healthz_ok(monkeypatch):
    client = make_client(monkeypatch, ScriptedProvider("SELECT 1"), FakeAnalyst())
    body = client.get("/healthz").json()
    assert body["status"] == "ok"
