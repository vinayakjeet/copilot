"""Defense layers for generated SQL, each toggleable so the safety suite can
measure what every layer alone actually catches.

The layers deliberately include the naive one. G1 is exactly what a careful
hobbyist ships: allowlist the leading keyword, reject semicolons. It stops the
obvious attacks and, as the matrix shows, waves through everything that
matters. Keeping it in the execution path (as the cheapest first check) keeps
that claim honest instead of theoretical.
"""

from __future__ import annotations

from dataclasses import dataclass

import sqlglot
from sqlglot import exp


@dataclass(frozen=True)
class Verdict:
    layer: str
    allowed: bool
    reason: str = ""

    def __bool__(self) -> bool:  # pragma: no cover - convenience only
        return self.allowed


# Functions that read server state or touch the filesystem. A pure analyst has
# no reason to call any of them; current_setting in particular is the payload
# the poisoned column comment asks for.
POLICY_FUNCTIONS = {
    "current_setting",
    "version",
    "inet_server_addr",
    "inet_client_addr",
    "pg_backend_pid",
    "pg_postmaster_start_time",
    "pg_conf_load_time",
    "pg_read_file",
    "pg_read_binary_file",
    "pg_ls_dir",
    "pg_stat_file",
    "pg_terminate_backend",
    "pg_cancel_backend",
    "pg_reload_conf",
    "pg_rotate_logfile",
    "lo_import",
    "lo_export",
    "readfile",
    "dblink",
    "dblink_exec",
    "pg_sleep",
    "pg_sleep_for",
    "pg_sleep_until",
}

# Relations an analyst never needs. Reading pg_shadow or pg_authid is the
# schema-escape class; the read-only role also refuses them, this just stops
# the attempt before it costs a round trip.
POLICY_RELATIONS = {
    "pg_shadow",
    "pg_authid",
    "pg_group",
    "pg_user",
    "pg_hba_file_rules",
    "pg_file_settings",
}


def naive_keyword_allowlist(sql_text: str) -> Verdict:
    """G1. Leading-keyword allowlist plus a semicolon scan.

    Documented weak on purpose: a WITH prefix passes regardless of what the CTEs
    do, which is precisely how a data-modifying CTE gets past this class of
    filter.
    """
    stripped = sql_text.strip().rstrip(";").strip()
    if not stripped:
        return Verdict("G1-naive", False, "empty statement")
    if ";" in stripped:
        return Verdict("G1-naive", False, "multiple statements")
    first = stripped.split(None, 1)[0].lower().strip("(")
    if first in ("select", "with"):
        return Verdict("G1-naive", True)
    return Verdict("G1-naive", False, f"leading keyword {first!r} not allowed")


def _walk_functions(node: exp.Expression) -> set[str]:
    names: set[str] = set()
    for f in node.find_all(exp.Anonymous):
        if f.name:
            names.add(f.name.lower())
    for f in node.find_all(exp.Func):
        sql_name = f.sql_name().lower()
        if sql_name:
            names.add(sql_name)
    return names


def _is_read_shape(node: exp.Expression | None, depth: int = 0) -> bool:
    """True for anything that only reads: SELECT (CTEs allowed) and set
    operations over those. Analyst questions legitimately arrive as
    UNION ALL of two SELECTs, and refusing those would trade real utility
    for nothing."""
    if depth > 8 or node is None:
        return False
    if isinstance(node, exp.Select):
        return True
    if isinstance(node, exp.Subquery):
        return _is_read_shape(node.this, depth + 1)
    if type(node).__name__ == "With":
        return True  # bare WITH handled through Select below
    if isinstance(node, exp.Union):
        left_ok = _is_read_shape(node.this, depth + 1)
        right_ok = _is_read_shape(node.expression, depth + 1)
        if not (left_ok and right_ok):
            return False
        # A UNION carries its own optional ORDER BY/LIMIT in .args; both sides
        # already checked, so it stays a read.
        return not node.args.get("into")
    return False


def static_analysis(sql_text: str) -> Verdict:
    """G2. Parse with sqlglot and enforce the statement shape.

    Fails closed: anything unparseable, anything whose root is not a SELECT,
    any mutating CTE, and every function on the policy list are rejected here
    before the database ever sees them.
    """
    try:
        statements = sqlglot.parse(sql_text, read="postgres")
    except Exception as exc:  # sqlglot raises ParseError subclasses
        return Verdict("G2-static", False, f"unparseable: {type(exc).__name__}")

    statements = [s for s in statements if s is not None]
    if len(statements) != 1:
        return Verdict("G2-static", False, f"{len(statements)} statements parsed")

    root = statements[0]
    if not _is_read_shape(root):
        kind = type(root).__name__
        return Verdict("G2-static", False, f"root statement is {kind}, only SELECT allowed")

    # For unions, every leaf gets the CTE / INTO treatment individually.
    leaves: list[exp.Expression] = []
    if isinstance(root, exp.Union):

        def collect(n: exp.Expression) -> None:
            if isinstance(n, exp.Union):
                collect(n.this)
                collect(n.expression)
            else:
                leaves.append(n)

        collect(root)
    else:
        leaves = [root]

    for leaf in leaves:
        verdict = _check_select_leaf(leaf)
        if not verdict.allowed:
            return verdict

    return Verdict("G2-static", True)


def _check_select_leaf(leaf: exp.Expression) -> Verdict:
    """Shape checks for one SELECT leaf: CTE bodies, INTO, policy functions,
    system relations."""
    if not isinstance(leaf, exp.Select):
        kind = type(leaf).__name__
        return Verdict("G2-static", False, f"statement part is {kind}, only SELECT allowed")
    if leaf.args.get("into"):
        return Verdict("G2-static", False, "SELECT INTO writes a table")

    # args["with_"] (sqlglot 30+) is a single With node whose expressions are
    # the CTEs; each CTE's .this is the statement it defines.
    with_node = leaf.args.get("with_") or leaf.args.get("with")
    if with_node is not None:
        for cte in with_node.expressions:
            cte_body = cte.this
            if not isinstance(cte_body, exp.Select):
                kind = type(cte_body).__name__
                return Verdict(
                    "G2-static", False, f"CTE body is {kind}, only SELECT allowed"
                )
            if cte_body.args.get("into"):
                return Verdict("G2-static", False, "SELECT INTO inside a CTE")

    functions = {name for name in _walk_functions(leaf) if name}
    hit = sorted(functions & POLICY_FUNCTIONS)
    if hit:
        return Verdict("G2-static", False, f"policy function(s): {', '.join(hit)}")

    relations = {rel.name.lower() for rel in leaf.find_all(exp.Table)}
    hit_rel = sorted(relations & POLICY_RELATIONS)
    if hit_rel:
        return Verdict("G2-static", False, f"system relation(s): {', '.join(hit_rel)}")

    return Verdict("G2-static", True)


def session_hardening_note() -> Verdict:
    """G3 exists at the role and transaction level, not in this module.

    Kept as a named constant so the matrix can reference the layer without the
    code pretending a Python function enforces it. See warehouse/provision.py
    for the role definition and copilot/analyst.py for per-transaction settings.
    """
    return Verdict("G3-session", True, "enforced by role grants and transaction defaults")


def sanitize_error(sqlstate: str | None, message: str) -> str:
    """G5. What reaches the browser after Postgres rejects something.

    Raw messages leak schema details, constraint names and sometimes values
    ('duplicate key value violates unique constraint customers_email_key').
    The analyst loop still sees the full error privately; sanitisation applies
    only to the client surface.
    """
    family = (sqlstate or "")[:2]
    specific = {
        "42501": "blocked by the read-only role",
        "25006": "write attempted in a read-only transaction",
        "57014": "query cancelled or exceeded its time limit",
        "57P03": "warehouse connection limit reached",
        "40P01": "query deadlocked and was rolled back",
        "53300": "too many connections to the warehouse",
        "53200": "warehouse out of memory for this query",
    }
    if sqlstate in specific:
        return specific[sqlstate]
    if family == "42":
        return "statement could not be executed against the warehouse"
    if family == "08":
        return "warehouse connection failed"
    return f"query failed ({sqlstate or 'unknown'})"


def guard_summary(verdicts: list[Verdict]) -> str:
    blocked = [v.layer for v in verdicts if not v.allowed]
    return "blocked at " + ", ".join(blocked) if blocked else "passed all layers"
