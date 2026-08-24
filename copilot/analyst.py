"""Executes approved statements against the warehouse.

Two pools, on purpose. The query pool runs as copilot_readonly with per-request
statement timeouts and a hard row cap. The control pool exists so cancellation
is a real SQL statement (pg_cancel_backend) aimed at the specific backend pid,
which is what makes the stop button's server-side claim checkable; the driver
level cancel is the fallback for hosts where the signal grant is unavailable.
"""

from __future__ import annotations

import asyncio
import time

import psycopg
import structlog
from psycopg_pool import AsyncConnectionPool

logger = structlog.get_logger(__name__)


class ExecutionError(Exception):
    def __init__(self, sqlstate: str | None, message: str, sanitized: str) -> None:
        super().__init__(message)
        self.sqlstate = sqlstate
        self.message = message  # full text, for the correction loop's eyes only
        self.sanitized = sanitized  # what the browser may see


class QueryCancelled(ExecutionError):
    pass


class Analyst:
    def __init__(
        self,
        ro_url: str,
        statement_timeout_ms: int,
        row_cap: int,
        pool_size: int = 4,
    ) -> None:
        self._ro_url = ro_url
        self._timeout_ms = statement_timeout_ms
        self._row_cap = row_cap
        self._pool_size = pool_size
        self._pool: AsyncConnectionPool | None = None
        self._control_pool: AsyncConnectionPool | None = None
        self._pids: dict[str, int] = {}
        self._conns: dict[str, psycopg.AsyncConnection] = {}

    async def warm(self) -> float:
        started = time.monotonic()
        if self._pool is None:
            self._pool = AsyncConnectionPool(
                self._ro_url,
                min_size=1,
                max_size=self._pool_size,
                open=False,
                kwargs={"autocommit": True},
            )
            await self._pool.open(wait=True, timeout=15)
            self._control_pool = AsyncConnectionPool(
                self._ro_url,
                min_size=1,
                max_size=1,
                open=False,
                kwargs={"autocommit": True},
            )
            await self._control_pool.open(wait=True, timeout=15)
        async with self._pool.connection() as conn:
            await conn.execute("SELECT 1")
        return round((time.monotonic() - started) * 1000, 1)

    async def close(self) -> None:
        for pool in (self._pool, self._control_pool):
            if pool is not None:
                await pool.close()
        self._pool = None
        self._control_pool = None

    async def execute(self, run_id: str, statement: str) -> dict:
        """Runs one approved SELECT. Returns columns, rows and timings.

        The row cap is enforced while fetching rather than by rewriting the
        user's SQL: appending LIMIT to arbitrary generated text is its own
        injection bug. Fetching past the cap simply stops and reports
        truncated, and the enclosing read-only transaction rolls back the rest.
        """
        assert self._pool is not None, "analyst not warmed"
        started = time.monotonic()

        try:
            async with self._pool.connection() as conn:
                self._conns[run_id] = conn
                pid_cur = conn.cursor()
                await pid_cur.execute("SELECT pg_backend_pid()")
                pid = (await pid_cur.fetchone())[0]
                self._pids[run_id] = pid
                logger.info("exec.start", run_id=run_id, backend_pid=pid)

                # read_only set here as well as at the role: belt and braces,
                # because a future config change dropping the role default
                # would otherwise silence this layer without anyone noticing.
                await conn.set_read_only(True)
                cur = conn.cursor()
                async with conn.transaction():
                    # SET does not take bind parameters, so the timeout goes
                    # in as a validated integer literal.
                    timeout_s = int(self._timeout_ms)
                    await cur.execute(
                        f"SET LOCAL statement_timeout = {timeout_s}"
                    )
                    fetch_started = time.monotonic()
                    await cur.execute(statement)

                    columns = [d.name for d in cur.description or []]
                    rows: list[list] = []
                    while True:
                        batch = await cur.fetchmany(200)
                        if not batch:
                            break
                        rows.extend([list(r) for r in batch])
                        if len(rows) > self._row_cap:
                            break

                truncated = len(rows) > self._row_cap
                rows = rows[: self._row_cap]

                wall_ms = round((time.monotonic() - started) * 1000, 1)
                logger.info(
                    "exec.done",
                    run_id=run_id,
                    rows=len(rows),
                    truncated=truncated,
                    wall_ms=wall_ms,
                )
                return {
                    "columns": columns,
                    "rows": rows,
                    "truncated": truncated,
                    "wall_ms": wall_ms,
                    "fetch_ms": round((time.monotonic() - fetch_started) * 1000, 1),
                    "backend_pid": pid,
                }
        except asyncio.CancelledError:
            raise
        except psycopg.errors.QueryCanceled as exc:
            raise QueryCancelled(
                exc.sqlstate, str(exc), "query cancelled or exceeded its time limit"
            ) from exc
        except psycopg.errors.Error as exc:
            from copilot.guard import sanitize_error

            raise ExecutionError(exc.sqlstate, str(exc), sanitize_error(exc.sqlstate, "")) from exc
        finally:
            self._pids.pop(run_id, None)
            self._conns.pop(run_id, None)

    async def cancel(self, run_id: str) -> bool:
        """Stop an in-flight execution. Returns whether anything was cancelled."""
        pid = self._pids.get(run_id)
        cancelled = False
        if pid is not None and self._control_pool is not None:
            try:
                async with self._control_pool.connection() as ctrl:
                    cur = ctrl.cursor()
                    await cur.execute("SELECT pg_cancel_backend(%s)", (pid,))
                    row = await cur.fetchone()
                    cancelled = bool(row[0])
            except psycopg.errors.Error as exc:
                logger.warning("cancel.sql_failed", run_id=run_id, error=str(exc))
        conn = self._conns.get(run_id)
        if conn is not None:
            try:
                conn.cancel()
                cancelled = True
            except Exception:  # noqa: BLE001 - cancel races execution finishing
                pass
        if pid is not None:
            logger.info("exec.cancel", run_id=run_id, backend_pid=pid, via_sql=cancelled)
        return cancelled

    def known_pid(self, run_id: str) -> int | None:
        return self._pids.get(run_id)
