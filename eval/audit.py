"""The label-audit pass over the eval set, recorded question by question.

    uv run python -m eval.audit

Method: after writing the sixty gold statements, every question got an
independently written second formulation (verify_sql). Both run against the
warehouse; identical results mean the label stands. Anything else went to
adjudication, decided by hand with a note below. The distinction that matters
in the counts:

- label defect: the gold answer itself was wrong or the question ambiguous.
- verification defect: gold was right but the second formulation was
  nondeterministic, mis-shaped, or tripped a guard. These say something about
  how easy it is to write a bad check, not about the labels.

Output: eval/label_audit.jsonl plus the printed summary used in the README.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

QUESTIONS = Path(__file__).resolve().parent / "questions.jsonl"
OUT = Path(__file__).resolve().parent / "label_audit.jsonl"

# Adjudication notes, keyed by question id. Everything not listed agreed on
# the first mechanical pass.
ADJUDICATIONS: dict[str, dict[str, str]] = {
    "q007": {
        "kind": "label",
        "note": "'Paid in cash' conflated order payment_mode with captured payments; cancelled COD orders fail settlement. Question reworded to placement-time payment mode.",
    },
    "q008": {
        "kind": "label",
        "note": "Seed gives some open tickets a stale resolved_at, so 'open' and 'never resolved' were different populations. Label redefined to resolved_at IS NULL.",
    },
    "q011": {
        "kind": "verification",
        "note": "Second formulation guessed at the flag formula from zone ids instead of reading it; replaced with an equivalent boolean aggregation.",
    },
    "q022": {
        "kind": "verification",
        "note": "Original verify joined payments to a fails subquery ON false, returning zeros. Rewritten with lateral counts per method.",
    },
    "q026": {
        "kind": "label",
        "note": "No resolved ticket carries a CSAT score under the seed rules, so the two-row comparison silently compared one row against two. Gold now filters empty groups explicitly.",
    },
    "q028": {"kind": "verification", "note": "UNION ALL of two SELECTs is read-shaped; guard taught to allow it, verify kept."},
    "q034": {
        "kind": "verification",
        "note": "Ties between months picked different winners depending on plan. Added month ASC tiebreakers to both formulations.",
    },
    "q035": {
        "kind": "verification",
        "note": "Boolean versus word labels ('False'/'rain') and slightly different null filters. Unified on named labels.",
    },
    "q036": {
        "kind": "verification",
        "note": "Verify computed average per city-day where gold sums total GMV; also boolean text casing. Rewrote as a join against a flags list.",
    },
    "q040": {
        "kind": "verification",
        "note": "ratings has no restaurant_id; original join never ran. Rerouted through orders, rounding aligned.",
    },
    "q042": {
        "kind": "label",
        "note": "'Menu items' was ambiguous about availability; denominator differed between formulations. Question pinned to available items.",
    },
    "q047": {
        "kind": "verification",
        "note": "max() ties across cities made LIMIT 1 nondeterministic; city and month tiebreakers added.",
    },
    "q050": {
        "kind": "verification",
        "note": "Verify projected extra columns so row shapes disagreed despite equal shares. Projected to the same shape.",
    },
    "q053": {
        "kind": "verification",
        "note": "Several months share the worst cancel rate; rank needed a month DESC tiebreaker, and the first rewrite grouped wrongly.",
    },
    "q055": {"kind": "verification", "note": "Same UNION root-shape issue as q028; guard updated rather than the query."},
    "q057": {"kind": "verification", "note": "Same UNION root-shape issue as q028; guard updated rather than the query."},
    "q058": {
        "kind": "verification",
        "note": "Quarter labels rendered as text versus timestamps. Verify now derives quarter starts as timestamptz.",
    },
}


def main() -> int:
    questions = [
        json.loads(line)
        for line in QUESTIONS.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]

    records = []
    for q in questions:
        adj = ADJUDICATIONS.get(q["id"])
        records.append(
            {
                "id": q["id"],
                "difficulty": q["difficulty"],
                "verdict": "adjudicated" if adj else "agree",
                **({"kind": adj["kind"], "note": adj["note"]} if adj else {}),
            }
        )

    OUT.write_text(
        "\n".join(json.dumps(r, ensure_ascii=False) for r in records) + "\n",
        encoding="utf-8",
    )

    total = len(records)
    adjudicated = [r for r in records if r["verdict"] == "adjudicated"]
    labels = [r for r in adjudicated if r["kind"] == "label"]
    verifs = [r for r in adjudicated if r["kind"] == "verification"]

    print(f"questions: {total}")
    print(f"label defects corrected: {len(labels)} ({100 * len(labels) / total:.1f}%)")
    print(f"second-pass formulations corrected: {len(verifs)}")
    for r in labels:
        print(f"  {r['id']}: {r['note']}")
    print(f"\nwrote {OUT.name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
