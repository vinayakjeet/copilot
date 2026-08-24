"""Execution accuracy of generated SQL against gold results, over N runs.

    uv run python -m eval.run_eval [--runs 3] [--limit 10]

Every question is sent to the model with the same schema-linking and untrusted
tagging the app uses, the reply is shaped and policy-checked exactly as in
production, then executed against the live warehouse beside the gold result.
A candidate passes when its rows equal gold's, order-sensitively only when the
question demands order.

Accuracy is reported on the corrected label set (post-audit) and, for the four
questions whose labels changed at adjudication, on their raw pre-audit labels
as well. Model, date and run count are printed because an undated accuracy
number says nothing.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import json
import statistics
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import psycopg  # noqa: E402

from copilot.config import get_settings  # noqa: E402
from copilot.generate import build_messages, extract_statement, looks_like_sql  # noqa: E402
from copilot.guard import static_analysis  # noqa: E402
from copilot.schema_linking import link, load_catalog  # noqa: E402
from llm import ChatClient, make_provider  # noqa: E402

HERE = Path(__file__).resolve().parent
RESULTS = HERE / "results.json"


def load_questions() -> list[dict]:
    return [
        json.loads(line)
        for line in (HERE / "questions.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def normalise(rows: list[tuple] | None, order_sensitive: bool) -> list[list[str]] | None:
    if rows is None:
        return None
    out = [[("" if v is None else str(v)) for v in row] for row in rows]
    if not order_sensitive:
        out = sorted(out)
    return out


class Runner:
    def __init__(self, ro_url: str) -> None:
        # A single-connection pool rather than a bare connection: Neon drops
        # long-lived sockets, and fifteen minutes of paced calls is exactly
        # where that bites. The pool reconnects transparently.
        from psycopg_pool import AsyncConnectionPool

        self._pool = AsyncConnectionPool(
            ro_url,
            min_size=1,
            max_size=1,
            open=False,
            kwargs={"autocommit": False},
            reconnect_timeout=30,
        )

    async def open(self) -> None:
        await self._pool.open(wait=True, timeout=30)

    async def close(self) -> None:
        await self._pool.close()

    async def execute(self, sql_text: str) -> tuple[list[tuple] | None, str | None]:
        # Two attempts: Neon drops long-lived sockets, and the first failure
        # is usually the pool noticing. The second attempt rides a fresh
        # connection and almost always lands.
        last_err = "unknown"
        for _attempt in range(2):
            async with self._pool.connection() as conn:
                cur = conn.cursor()
                try:
                    await cur.execute(sql_text)
                    rows = await cur.fetchall()
                    await conn.rollback()
                    return rows, None
                except psycopg.errors.Error as exc:
                    detail = f"{exc.sqlstate}: {str(exc)[:120]}"
                    with contextlib.suppress(Exception):
                        await conn.rollback()
                    if isinstance(exc, psycopg.OperationalError):
                        last_err = f"connection lost ({detail})"
                        continue
                    return None, detail
        return None, last_err

    async def score_question(self, client: ChatClient, catalog: dict, q: dict) -> dict:
        linked = link(q["question"], catalog)
        messages = build_messages(q["question"], linked.render(), tagged=True)
        started = time.monotonic()
        try:
            response = await client.complete(messages)
            raw_reply = response.text
        except Exception as exc:  # noqa: BLE001 - provider failures are a miss
            return {"id": q["id"], "outcome": "provider_error", "detail": str(exc)[:150]}

        latency_ms = round((time.monotonic() - started) * 1000, 1)
        try:
            statement = extract_statement(raw_reply)
        except ValueError:
            return {"id": q["id"], "outcome": "unshapable", "latency_ms": latency_ms}

        verdict = static_analysis(statement)
        if not verdict.allowed or not looks_like_sql(statement):
            return {
                "id": q["id"],
                "outcome": "policy_blocked",
                "reason": verdict.reason,
                "statement": statement[:200],
                "latency_ms": latency_ms,
            }

        cand_rows, err = await self.execute(statement)
        if err is not None:
            return {"id": q["id"], "outcome": "exec_error", "detail": err, "latency_ms": latency_ms}

        gold_rows, _ = await self.execute(q["gold_sql"])
        ok = (
            gold_rows is not None
            and normalise(cand_rows, q.get("order_sensitive", False))
            == normalise(gold_rows, q.get("order_sensitive", False))
        )
        return {
            "id": q["id"],
            "outcome": "pass" if ok else "wrong_rows",
            "latency_ms": latency_ms,
        }

    async def score_raw_slice(
        self, client: ChatClient, catalog: dict, questions: list[dict]
    ) -> list[dict]:
        """The four relabelled questions under their original labels."""
        out = []
        for q in questions:
            if not q.get("raw_gold_sql"):
                continue
            probe = {**q, "question": q["raw_question"], "gold_sql": q["raw_gold_sql"]}
            res = await self.score_question(client, catalog, probe)
            res["id"] = q["id"] + "@raw"
            out.append(res)
        return out


async def main_async(args: argparse.Namespace) -> int:
    from dotenv import load_dotenv

    load_dotenv(override=True)
    import os

    ro_url = os.environ.get("COPILOT_RO_URL")
    settings = get_settings()
    if not ro_url or settings.copilot_llm_provider not in ("tollgate", "groq"):
        print(
            "error: eval needs COPILOT_RO_URL and COPILOT_LLM_PROVIDER=tollgate|groq",
            file=sys.stderr,
        )
        return 2

    questions = load_questions()
    if args.limit:
        questions = questions[: args.limit]

    print(
        f"model={settings.copilot_model} via tollgate; "
        f"{len(questions)} questions x {args.runs} runs; "
        f"started {datetime.now(UTC).isoformat(timespec='seconds')}",
        flush=True,
    )

    catalog = await load_catalog(ro_url)
    client = ChatClient(make_provider(settings.copilot_llm_provider, settings))

    # Free-tier token budgets are per minute and shared with everything else
    # that used this key today. Running unpaced burns the window in a dozen
    # questions and then every answer comes back empty; a fixed gap under the
    # documented ceiling finishes slower and never trips.
    min_gap = 60.0 / args.rpm if args.rpm else 0.0
    next_ok = 0.0

    all_runs: list[list[dict]] = []
    raw_runs: list[list[dict]] = []
    runner = Runner(ro_url)
    await runner.open()
    try:
        for run_no in range(1, args.runs + 1):
            print(f"run {run_no}/{args.runs} ...", flush=True)
            outcomes = []
            for n, q in enumerate(questions, 1):
                now = asyncio.get_running_loop().time()
                if now < next_ok:
                    await asyncio.sleep(next_ok - now)
                next_ok = asyncio.get_running_loop().time() + min_gap
                res = await runner.score_question(client, catalog, q)
                outcomes.append(res)
                mark = res["outcome"]
                if mark != "pass":
                    detail = res.get("detail", res.get("reason", ""))
                    print(
                f"  [{n}/{len(questions)}] {q['id']} {mark} {detail}", flush=True
            )
            all_runs.append(outcomes)

        raw_qs = [q for q in load_questions() if q.get("raw_gold_sql")]
        raw_runs.append(await runner.score_raw_slice(client, catalog, raw_qs))
    finally:
        await runner.close()

    def accuracy(outcomes: list[dict]) -> float:
        return sum(1 for o in outcomes if o["outcome"] == "pass") / len(outcomes)

    accs = [accuracy(r) for r in all_runs]
    mean = statistics.mean(accs)
    stdev = statistics.stdev(accs) if len(accs) > 1 else 0.0

    failure_kinds: dict[str, int] = {}
    for r in all_runs:
        for o in r:
            if o["outcome"] != "pass":
                failure_kinds[o["outcome"]] = failure_kinds.get(o["outcome"], 0) + 1

    raw_accs = [accuracy(r) if r else 0.0 for r in raw_runs]

    payload = {
        "measured_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "model": settings.copilot_model,
        "runs": args.runs,
        "n_questions": len(questions),
        "accuracy_mean": round(mean, 4),
        "accuracy_stdev": round(stdev, 4),
        "per_run_accuracy": [round(a, 4) for a in accs],
        "failure_kinds": failure_kinds,
        "raw_label_accuracy": round(statistics.mean(raw_accs), 4),
        "corrected_label_accuracy": round(mean, 4),
        "outcomes": all_runs,
    }
    RESULTS.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    print(
        f"\naccuracy (corrected labels): {mean:.3f} +- {stdev:.3f} over {args.runs} runs"
    )
    print(
        f"accuracy (raw labels, {len(raw_qs)}-question slice): "
        f"{payload['raw_label_accuracy']:.3f}"
    )
    print(f"failures by kind: {failure_kinds}")
    print(f"wrote {RESULTS.name}")
    return 0


def main() -> int:
    import asyncio
    import sys as _sys

    if _sys.platform == "win32":
        policy = getattr(asyncio, "WindowsSelectorEventLoopPolicy", None)
        if policy is not None:
            asyncio.set_event_loop_policy(policy())

    parser = argparse.ArgumentParser()
    parser.add_argument("--runs", type=int, default=3)
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument(
        "--rpm",
        type=float,
        default=14.0,
        help="Requests per minute to pace at; keep below the provider ceiling.",
    )
    return asyncio.run(main_async(parser.parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())
