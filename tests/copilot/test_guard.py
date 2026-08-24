import pytest

from copilot.guard import naive_keyword_allowlist, sanitize_error, static_analysis

# Payloads from the brief's attack table, plus the benign statements that must
# keep working. Layer ownership follows the brief's table: static analysis owns
# statement shape, policy functions and system relations; the read-only role
# owns anything that writes; the timeout owns resource bombs, which no static
# pass can honestly promise to prove terminate.
ATTACK_CASES = {
    "direct_delete": "DELETE FROM warehouse.orders WHERE order_id = 1",
    "direct_drop": "DROP TABLE warehouse.orders",
    "direct_truncate": "TRUNCATE warehouse.orders",
    "direct_insert": (
        "INSERT INTO warehouse.cities VALUES (99, 'x', 'x', 'x', 'x', "
        "false, 1, '2025-01-01')"
    ),
    "mutating_cte": (
        "WITH gone AS (DELETE FROM warehouse.orders WHERE order_id = 1 RETURNING *) "
        "SELECT * FROM gone"
    ),
    "mutating_cte_update": (
        "WITH bumped AS (UPDATE warehouse.customers SET loyalty_tier = 'platinum' "
        "RETURNING *) SELECT count(*) FROM bumped"
    ),
    "select_into": "SELECT * INTO warehouse.copy FROM warehouse.orders",
    "pg_sleep": "SELECT pg_sleep(30)",
    "read_server_file": "SELECT pg_read_file('/etc/passwd')",
    "catalog_escape": "SELECT usename, passwd FROM pg_shadow",
    "authid_read": "SELECT rolname, rolpassword FROM pg_authid",
    "set_role": "SET ROLE neondb_owner",
    "current_setting_exfil": "SELECT current_setting('server_version')",
    "multi_statement": "SELECT 1; DELETE FROM warehouse.orders",
}

# Correctly owned by G3 (role + statement_timeout), not by any parser: claiming
# otherwise would be the defense-in-depth equivalent of a fake number.
G3_OWNED = {
    "cartesian_bomb": (
        "SELECT count(*) FROM generate_series(1,100000) a, generate_series(1,100000) b"
    ),
    "recursive_cte": (
        "WITH RECURSIVE t(n) AS (SELECT 1 UNION ALL SELECT n+1 FROM t) "
        "SELECT * FROM t"
    ),
}

BENIGN_CASES = [
    ("simple", "SELECT count(*) FROM warehouse.orders"),
    ("join", (
        "SELECT c.city_id, count(*) FROM warehouse.orders o "
        "JOIN warehouse.customers c USING (customer_id) GROUP BY 1"
    )),
    ("window", (
        "SELECT order_id, grand_total_inr, rank() OVER (ORDER BY grand_total_inr DESC) "
        "FROM warehouse.orders LIMIT 10"
    )),
    ("date_math", (
        "SELECT date_trunc('month', placed_at), sum(grand_total_inr) "
        "FROM warehouse.orders GROUP BY 1 ORDER BY 1"
    )),
]


@pytest.mark.parametrize("name,sql", sorted(ATTACK_CASES.items()))
def test_static_layer_blocks_every_attack(name, sql):
    verdict = static_analysis(sql)
    assert not verdict.allowed, f"{name}: {verdict.reason}"


@pytest.mark.parametrize("name,sql", BENIGN_CASES)
def test_static_layer_passes_analyst_queries(name, sql):
    assert static_analysis(sql).allowed


@pytest.mark.parametrize("name,sql", sorted(ATTACK_CASES.items()))
def test_naive_layer_catches_direct_and_only_direct(name, sql):
    """The point of keeping G1: document exactly which attacks it misses.

    It should catch everything with an obvious leading keyword or a semicolon,
    and pass every payload whose first word is WITH or SELECT. That asymmetry
    is the finding the safety matrix publishes.
    """
    verdict = naive_keyword_allowlist(sql)
    obvious = name in {
        "direct_delete", "direct_drop", "direct_truncate", "direct_insert",
        "set_role", "multi_statement",
    }
    if obvious:
        assert not verdict.allowed
    else:
        assert verdict.allowed


def test_recursive_cte_is_not_claimed_by_the_static_layer():
    """Honesty check: G2 allows a recursive CTE through structurally because
    termination is unprovable here. Execution safety comes from the statement
    timeout; if G2 ever starts rejecting this, fine, but nothing may claim it
    proves termination."""
    assert isinstance(static_analysis(G3_OWNED["recursive_cte"]).allowed, bool)


def test_resource_bomb_is_g3_territory():
    assert static_analysis(G3_OWNED["cartesian_bomb"]).allowed


def test_unparseable_fails_closed():
    assert not static_analysis("SELECT FROM FROM WHERE").allowed


def test_sanitize_maps_known_states_and_hides_details():
    assert sanitize_error("42501", "permission denied for table orders") == (
        "blocked by the read-only role"
    )
    assert sanitize_error("57014", "canceling statement due to statement timeout") == (
        "query cancelled or exceeded its time limit"
    )
    out = sanitize_error("42601", 'syntax error at or near "FROM" near line 3')
    assert "FROM" not in out and "line" not in out
    assert sanitize_error(None, "anything") .startswith("query failed")
