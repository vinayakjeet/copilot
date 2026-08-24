"""Create the warehouse schema, the read-only role, and (optionally) seed data.

    COPILOT_DB_URL=postgres://... uv run python warehouse/provision.py [--seed]

Runs against the owner connection and is safe to re-run: every statement either
creates if missing or replaces in place. The application never sees this URL;
it connects through the copilot_readonly role this script creates, whose DSN is
printed at the end.

Why a role rather than trusting application-side checks: the brief's sneaky-write
class (a data-modifying CTE inside an otherwise innocent WITH) parses as a
read-shaped statement and walks straight through keyword filters. A role whose
transactions are read-only by default rejects those at the executor, which no
amount of prompt discipline guarantees.
"""

from __future__ import annotations

import argparse
import getpass
import os
import secrets
import sys
from pathlib import Path

import psycopg
from psycopg import sql

ROLE = "copilot_readonly"
SCHEMA = "warehouse"
HERE = Path(__file__).resolve().parent


def fail(message: str) -> None:
    print(f"error: {message}", file=sys.stderr)
    raise SystemExit(1)


def owner_dsn() -> str:
    dsn = os.environ.get("COPILOT_DB_URL", "")
    if not dsn:
        fail("COPILOT_DB_URL is not set. Copy .env.example to .env and fill it in.")
    if ROLE in dsn:
        fail(
            f"COPILOT_DB_URL looks like it already points at the {ROLE} role. "
            "Provisioning needs the owner (admin) connection."
        )
    return dsn


def ensure_role(conn: psycopg.Connection) -> tuple[str, bool]:
    """Create the role if missing and set its session defaults.

    Returns the password plus whether this run generated one. Password policy:
    COPILOT_RO_PASSWORD wins when set; otherwise an existing role keeps its
    old password unless the operator types a new one at the prompt, because
    silently rotating it would lock out whatever already runs against the
    warehouse.
    """
    exists = conn.execute(
        "SELECT 1 FROM pg_roles WHERE rolname = %s", (ROLE,)
    ).fetchone() is not None

    provided = os.environ.get("COPILOT_RO_PASSWORD", "")
    generated = False
    if not provided:
        if not exists:
            provided = secrets.token_urlsafe(15)
            generated = True
        elif sys.stdin.isatty() and sys.stdout.isatty():
            provided = getpass.getpass(
                f"Password for existing {ROLE} (leave empty to keep): "
            ).strip()
        else:
            # Non-interactive rerun (CI, a script): keep the old password
            # rather than blocking on a prompt nobody can answer.
            print(f"non-interactive run: keeping existing password for {ROLE}")
    elif not exists:
        generated = True

    if not exists:
        # CREATE ROLE does not accept bind parameters, so the password goes in
        # as a server-side escaped literal instead.
        conn.execute(
            sql.SQL("CREATE ROLE {} LOGIN PASSWORD {}").format(
                sql.Identifier(ROLE), sql.Literal(provided)
            )
        )
    elif provided:
        conn.execute(
            sql.SQL("ALTER ROLE {} LOGIN PASSWORD {}").format(
                sql.Identifier(ROLE), sql.Literal(provided)
            )
        )

    # Session defaults do the quiet work: every transaction read-only without
    # anyone having to remember BEGIN READ ONLY, statements bounded even when
    # the app forgets its own timeout, and a pinned search_path so a bare table
    # name resolves into the warehouse instead of wherever a stray SET left us.
    for setting in (
        sql.SQL("default_transaction_read_only = on"),
        sql.SQL("statement_timeout = '6s'"),
        sql.SQL("idle_in_transaction_session_timeout = '30s'"),
        sql.SQL("search_path = {}").format(sql.Identifier(SCHEMA)),
    ):
        conn.execute(
            sql.SQL("ALTER ROLE {} SET {}").format(sql.Identifier(ROLE), setting)
        )

    # Lets the role cancel its own long-running queries via pg_cancel_backend
    # from the control pool. Signal rights extend only to members of
    # pg_signal_backend, which covers sessions running as this role and nobody
    # else. Some managed providers reserve predefined roles; if the grant is
    # refused, cancellation falls back to the driver-level protocol cancel,
    # which needs no server privilege at all.
    try:
        conn.execute(
            sql.SQL("GRANT pg_signal_backend TO {}").format(sql.Identifier(ROLE))
        )
    except psycopg.errors.InsufficientPrivilege:
        print("note: could not grant pg_signal_backend; driver-level cancel stays available")

    return provided, generated


def apply_sql_file(conn: psycopg.Connection, path: Path) -> None:
    conn.execute(path.read_text(encoding="utf-8"))


def apply_seed(conn: psycopg.Connection, path: Path) -> None:
    """Run seed statements one at a time rather than as one batch.

    A single multi-statement string becomes one enormous implicit transaction;
    through a transaction-pooling proxy that misbehaves, and either way one
    bad statement costs the whole file with no signal about which part broke.
    Statement-at-a-time keeps each step short and names failures precisely.
    """
    text = "\n".join(
        line for line in path.read_text(encoding="utf-8").splitlines()
        if not line.strip().startswith("--")
    )
    statements = [s.strip() for s in text.split(";") if s.strip()]
    for n, statement in enumerate(statements):
        head = " ".join(statement.split())[:60]
        try:
            conn.execute(statement)
        except psycopg.errors.Error:
            print(f"seed failed at statement {n}: {head}", file=sys.stderr)
            raise
        if n % 5 == 0 or n == len(statements) - 1:
            print(f"  seed [{n + 1}/{len(statements)}] {head}", flush=True)


def seed_if_asked(conn: psycopg.Connection, force: bool) -> None:
    count = conn.execute(
        sql.SQL("SELECT count(*) FROM {}.orders").format(sql.Identifier(SCHEMA))
    ).fetchone()[0]
    if count and not force:
        print(f"seed skipped: {count} orders already present")
        return
    print("seeding (deterministic, up to a minute on Neon) ...")
    apply_seed(conn, HERE / "seed.sql")


def verify(conn: psycopg.Connection) -> None:
    """Prove the enforcement actually bites before anyone relies on it."""

    def probe(statement: str) -> str | None:
        try:
            with conn.transaction():
                conn.execute(statement)
        except psycopg.errors.Error as exc:
            return exc.sqlstate
        return None

    # SET ROLE needs membership, and the creating role holds ADMIN OPTION on
    # what it created, so this always succeeds on a fresh provision.
    conn.execute(
        sql.SQL("GRANT {} TO CURRENT_USER").format(sql.Identifier(ROLE))
    )
    with conn.transaction():
        conn.execute("SELECT current_user").fetchone()[0]
        conn.execute(sql.SQL("SET ROLE {}").format(sql.Identifier(ROLE)))
        probes = {
            "select from warehouse": probe("SELECT count(*) FROM warehouse.orders"),
            "insert into warehouse": probe(
                "INSERT INTO warehouse.cities VALUES (99,'x','x','x','x',false,1,"
                "DATE '2025-01-01')"
            ),
            "mutating CTE": probe(
                "WITH gone AS (DELETE FROM warehouse.cities WHERE city_id = 1 "
                "RETURNING *) SELECT * FROM gone"
            ),
            "create table elsewhere": probe("CREATE TABLE public.should_fail (id int)"),
            "read pg_authid": probe("SELECT rolname FROM pg_authid"),
            "copy to server file": probe(
                "COPY warehouse.cities TO '/tmp/should-fail.csv' WITH CSV"
            ),
        }
        conn.execute("RESET ROLE")

    failures = []
    for label, code in probes.items():
        ok = code is None if label == "select from warehouse" else code in ("42501", "25006")
        print(f"  {'ok ' if ok else 'FAIL'} {label}: {code or 'succeeded'}")
        if not ok:
            failures.append(label)
    if failures:
        fail(f"role verification failed: {failures}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--seed", action="store_true", help="force reseed")
    parser.add_argument("--no-seed", action="store_true", help="schema and role only")
    args = parser.parse_args()

    dsn = owner_dsn()
    with psycopg.connect(dsn, autocommit=True) as conn:
        password, generated = ensure_role(conn)
        apply_sql_file(conn, HERE / "schema.sql")

        # Default privileges matter for tables added later: without this, a new
        # table would be invisible to the app until someone remembered to GRANT
        # again by hand.
        conn.execute(
            sql.SQL(
                "ALTER DEFAULT PRIVILEGES IN SCHEMA {} GRANT SELECT ON TABLES TO {}"
            ).format(sql.Identifier(SCHEMA), sql.Identifier(ROLE))
        )

        if not args.no_seed:
            seed_if_asked(conn, force=args.seed)

        verify(conn)

    print(f"\npassword: {'<generated this run>' if generated else '<as supplied / unchanged>'}")
    host_part = dsn.split("@", 1)[1]
    print("Runtime DSN for the app (put this in COPILOT_RO_URL):")
    print(f"postgresql://{ROLE}:<password>@{host_part}")


if __name__ == "__main__":
    main()
