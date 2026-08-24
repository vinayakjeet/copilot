"""Execute every gold and verify statement; report mismatches and empties.

    COPILOT_RO_URL=... uv run python -m eval.validate

This is the mechanical half of the label audit: two independently written
formulations of each question must return identical results, otherwise the
label goes to adjudication. Run it after any change to seed.sql.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import psycopg

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from copilot.guard import static_analysis  # noqa: E402

QUESTIONS = Path(__file__).resolve().parent / "questions.jsonl"


def load() -> list[dict]:
    out = []
    for line in QUESTIONS.read_text(encoding="utf-8").splitlines():
        if line.strip():
            out.append(json.loads(line))
    return out


def normalise(rows: list[tuple], order_sensitive: bool) -> list[list[str]]:
    rendered = [[("" if v is None else str(v)) for v in row] for row in rows]
    if not order_sensitive:
        rendered = sorted(rendered)
    return rendered


def main() -> int:
    from dotenv import load_dotenv

    load_dotenv(override=True)
    import os

    url = os.environ.get("COPILOT_RO_URL")
    if not url:
        print("error: set COPILOT_RO_URL", file=sys.stderr)
        return 2

    problems = 0
    with psycopg.connect(url, autocommit=True) as conn:
        cur = conn.cursor()
        for q in load():
            qid = q["id"]
            for kind in ("gold_sql", "verify_sql"):
                sql_text = q[kind]
                verdict = static_analysis(sql_text)
                if not verdict.allowed:
                    print(f"{qid} {kind}: GUARD BLOCKED ({verdict.reason})")
                    problems += 1
                    continue
                try:
                    cur.execute(sql_text)
                    rows = cur.fetchall()
                except psycopg.errors.Error as exc:
                    print(f"{qid} {kind}: ERROR {exc.sqlstate} {str(exc)[:110]}")
                    problems += 1
                    continue
                if len(rows) == 0:
                    print(f"{qid} {kind}: EMPTY")

            try:
                cur.execute(q["gold_sql"])
                gold_rows = cur.fetchall()
                cur.execute(q["verify_sql"])
                ver_rows = cur.fetchall()
            except psycopg.errors.Error:
                continue

            g = normalise(gold_rows, q.get("order_sensitive", False))
            v = normalise(ver_rows, q.get("order_sensitive", False))
            if g != v:
                print(f"{qid}: MISMATCH gold={g[:3]} verify={v[:3]}")
                problems += 1

    print(f"\n{len(load())} questions checked, {problems} problem(s)")
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
