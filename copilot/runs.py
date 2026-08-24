"""Run registry: the state behind one question, and the stop button's target.

A run moves generating -> awaiting_approval -> executing -> done, with failed
and cancelled reachable from anywhere. The registry owns both cancellation
mechanisms: aborting the generation task tears down the upstream HTTP stream,
and cancelling an execution goes through Analyst.cancel, which fires
pg_cancel_backend at the exact backend running this run's query.
"""

from __future__ import annotations

import asyncio
import time
import uuid

import structlog

logger = structlog.get_logger(__name__)

PHASES = (
    "generating",
    "awaiting_approval",
    "executing",
    "done",
    "rejected",
    "failed",
    "cancelled",
)


class RunState:
    def __init__(self, question: str) -> None:
        self.id = uuid.uuid4().hex[:12]
        self.question = question
        self.phase = "generating"
        self.created_at = time.monotonic()
        self.stop_event = asyncio.Event()
        self.generation_task: asyncio.Task | None = None
        self.execution_task: asyncio.Task | None = None
        self.statement: str = ""
        self.attempts: list[str] = []
        self.timings: dict[str, float] = {}
        self.linked_tables: list[str] = []
        self.tagged = True
        self.schema_text: str = ""
        # Set by the caller once the analyst exists; stop() needs it to reach
        # pg_cancel_backend without the registry importing the pool itself.
        self.analyst: object | None = None

    @property
    def age_ms(self) -> float:
        return round((time.monotonic() - self.created_at) * 1000, 1)

    def set_phase(self, phase: str) -> None:
        if phase not in PHASES:
            raise ValueError(f"unknown phase {phase!r}")
        self.phase = phase


class RunRegistry:
    def __init__(self, max_age_s: float = 900.0) -> None:
        self._runs: dict[str, RunState] = {}
        self._max_age_s = max_age_s

    def create(self, question: str) -> RunState:
        self._gc()
        state = RunState(question)
        self._runs[state.id] = state
        return state

    def get(self, run_id: str) -> RunState | None:
        return self._runs.get(run_id)

    async def stop(self, run_id: str) -> dict:
        """User pressed stop. Whatever phase the run is in, stop means stop.

        Returns a small report so the API can tell the client what actually
        happened server-side; the UI renders it verbatim.
        """
        state = self.get(run_id)
        if state is None:
            return {"stopped": False, "reason": "unknown run"}

        if state.phase == "generating":
            # The stop event works even when the task has not started yet: the
            # provider checks it between chunks, so setting it here stops a
            # stream that begins afterwards too.
            state.stop_event.set()
            if state.generation_task is not None:
                state.generation_task.cancel()
                try:
                    await state.generation_task
                except asyncio.CancelledError:
                    pass
                except Exception:  # noqa: BLE001 - the run is being torn down
                    pass
            state.set_phase("cancelled")
            logger.info("run.stopped", run_id=run_id, where="generation")
            return {"stopped": True, "where": "generation", "age_ms": state.age_ms}

        if state.phase == "executing":

            analyst = state.analyst
            via_sql = False
            if analyst is not None:
                via_sql = await analyst.cancel(run_id)
            if state.execution_task is not None:
                state.execution_task.cancel()
                try:
                    await state.execution_task
                except asyncio.CancelledError:
                    pass
                except Exception:  # noqa: BLE001
                    pass
            state.set_phase("cancelled")
            logger.info(
                "run.stopped", run_id=run_id, where="execution", via_pg_cancel_backend=via_sql
            )
            return {
                "stopped": True,
                "where": "execution",
                "via_pg_cancel_backend": via_sql,
                "age_ms": state.age_ms,
            }

        if state.phase == "awaiting_approval":
            state.set_phase("cancelled")
            return {"stopped": True, "where": "preview", "age_ms": state.age_ms}

        return {"stopped": False, "reason": f"run already {state.phase}"}

    def _gc(self) -> None:
        stale = [
            rid for rid, s in self._runs.items() if s.age_ms > self._max_age_s * 1000
        ]
        for rid in stale:
            self._runs.pop(rid, None)
