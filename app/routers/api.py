from __future__ import annotations

import asyncio
import json
import time
from collections.abc import AsyncIterator

import structlog
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from copilot.analyst import ExecutionError, QueryCancelled
from copilot.chart import chart_descriptor, chart_title
from copilot.generate import build_messages, extract_statement, looks_like_sql
from copilot.guard import Verdict, naive_keyword_allowlist, static_analysis
from llm.types import (
    ChatMessage,
    GenerationCancelled,
    ProviderClientError,
    ProviderConfigError,
    ProviderError,
)

logger = structlog.get_logger(__name__)

router = APIRouter(prefix="/api")

SSE_HEADERS = {"Cache-Control": "no-cache", "X-Accel-Buffering": "no"}


class Question(BaseModel):
    question: str = Field(min_length=3, max_length=500)


class RunRef(BaseModel):
    run_id: str


def _event(name: str, **payload) -> bytes:
    return f"event: {name}\ndata: {json.dumps(payload, default=str)}\n\n".encode()


class GenerationHandle:
    """One model call, running as a task so POST /api/stop can kill it.

    Tokens flow through a queue to the SSE writer while the full reply
    accumulates on the handle, because both consumers need it: the writer wants
    deltas now, the caller wants the assembled statement after.
    """

    def __init__(self) -> None:
        self.queue: asyncio.Queue = asyncio.Queue()
        self.chunks: list[str] = []
        self.outcome = "running"
        self.error = ""
        self.created = time.monotonic()
        self.task: asyncio.Task | None = None


def _start_generation(
    state, run_state, messages: list[ChatMessage]
) -> GenerationHandle:
    handle = GenerationHandle()

    async def produce() -> None:
        try:
            async for delta in state.llm_client.stream(
                messages, stop=run_state.stop_event
            ):
                handle.chunks.append(delta)
                await handle.queue.put(("token", {"text": delta}))
            handle.outcome = "done"
        except GenerationCancelled:
            handle.outcome = "cancelled"
        except (ProviderError, ProviderClientError, ProviderConfigError) as exc:
            handle.outcome = "error"
            handle.error = str(exc)[:300]
        except Exception as exc:  # noqa: BLE001 - the stream must always terminate
            logger.exception("generation.crash", run_id=run_state.id)
            handle.outcome = "error"
            handle.error = f"{type(exc).__name__}: {exc}"
        finally:
            await handle.queue.put(None)

    handle.task = asyncio.create_task(produce())
    return handle


async def _relay(handle: GenerationHandle) -> AsyncIterator[bytes]:
    while True:
        item = await handle.queue.get()
        if item is None:
            break
        yield _event(item[0], **item[1])
    if handle.outcome == "cancelled":
        yield _event("stopped", where="generation")
    elif handle.outcome == "error":
        yield _event("error", message=handle.error)


@router.post("/query")
async def query(body: Question, request: Request):
    state = request.app.state
    if not state.readiness.ready:
        raise HTTPException(status_code=503, detail=state.readiness.snapshot())

    from copilot.schema_linking import link

    run_state = state.registry.create(body.question)
    linked = link(body.question, state.catalog)
    run_state.linked_tables = linked.table_names()
    run_state.schema_text = linked.render()

    async def stream() -> AsyncIterator[bytes]:
        yield _event(
            "meta", run_id=run_state.id, tables=run_state.linked_tables, tagged=True
        )

        handle = _start_generation(
            state,
            run_state,
            build_messages(body.question, run_state.schema_text),
        )
        async for chunk in _relay(handle):
            yield chunk
        run_state.timings["generate_ms"] = round((time.monotonic() - handle.created) * 1000, 1)

        if handle.outcome == "cancelled":
            run_state.set_phase("cancelled")
            return
        if handle.outcome != "done":
            run_state.set_phase("failed")
            return

        try:
            statement = extract_statement("".join(handle.chunks))
        except ValueError as exc:
            logger.info("generation.unshapable", run_id=run_state.id, error=str(exc))
            handle = _start_generation(
                state,
                run_state,
                build_messages(body.question, run_state.schema_text),
            )
            async for chunk in _relay(handle):
                yield chunk
            if handle.outcome != "done":
                run_state.set_phase("failed")
                return
            try:
                statement = extract_statement("".join(handle.chunks))
            except ValueError as exc2:
                run_state.set_phase("failed")
                yield _event("error", message=str(exc2))
                return

        g1: Verdict = naive_keyword_allowlist(statement)
        g2: Verdict = static_analysis(statement)

        if not looks_like_sql(statement) or not g2.allowed:
            reason = g2.reason or "not a SELECT statement"
            yield _event("guard_blocked", layer=g2.layer, reason=reason)
            handle = _start_generation(
                state,
                run_state,
                build_messages(
                    body.question,
                    run_state.schema_text,
                    error_context=f"statement rejected by policy: {reason}",
                ),
            )
            async for chunk in _relay(handle):
                yield chunk
            if handle.outcome != "done":
                run_state.set_phase("failed")
                return
            try:
                statement = extract_statement("".join(handle.chunks))
            except ValueError as exc3:
                run_state.set_phase("failed")
                yield _event("error", message=str(exc3))
                return
            g1 = naive_keyword_allowlist(statement)
            g2 = static_analysis(statement)

        run_state.statement = statement
        run_state.attempts.append(statement)
        run_state.set_phase("awaiting_approval")
        yield _event(
            "preview",
            sql=statement,
            attempts=len(run_state.attempts),
            naive_allowed=g1.allowed,
            blocked=(not g2.allowed),
            reason="" if g2.allowed else g2.reason,
        )

    return StreamingResponse(stream(), media_type="text/event-stream", headers=SSE_HEADERS)


@router.post("/approve")
async def approve(body: RunRef, request: Request):
    state = request.app.state
    run_state = state.registry.get(body.run_id)
    if run_state is None:
        raise HTTPException(status_code=404, detail="unknown run")
    if run_state.phase != "awaiting_approval":
        raise HTTPException(status_code=409, detail=f"run is {run_state.phase}")

    g2: Verdict = static_analysis(run_state.statement)
    if not g2.allowed:
        run_state.set_phase("failed")
        raise HTTPException(status_code=422, detail=g2.reason or "policy rejected")

    async def stream() -> AsyncIterator[bytes]:
        run_state.set_phase("executing")
        run_state.analyst = state.analyst
        exec_started = time.monotonic()
        try:
            result = await state.analyst.execute(run_state.id, run_state.statement)
        except QueryCancelled:
            run_state.set_phase("cancelled")
            yield _event("stopped", where="execution")
            return
        except asyncio.CancelledError:
            # Client walked away mid-query. Cancelling server-side is the whole
            # point of the stop story, so a disconnect gets the same treatment.
            await state.analyst.cancel(run_state.id)
            run_state.set_phase("cancelled")
            raise
        except ExecutionError as exc:
            yield _event("exec_error", sanitized=exc.sanitized)
            handle = _start_generation(
                state,
                run_state,
                build_messages(
                    run_state.question,
                    run_state.schema_text,
                    error_context=f"{exc.sqlstate}: {exc.message[:400]}",
                ),
            )
            async for chunk in _relay(handle):
                yield chunk
            if handle.outcome != "done":
                run_state.set_phase("failed")
                return
            try:
                corrected = extract_statement("".join(handle.chunks))
            except ValueError as val_err:
                run_state.set_phase("failed")
                yield _event("error", message=str(val_err))
                return
            g2c = static_analysis(corrected)
            run_state.statement = corrected
            run_state.attempts.append(corrected)
            run_state.set_phase("awaiting_approval")
            yield _event(
                "correction_preview",
                sql=corrected,
                attempts=len(run_state.attempts),
                blocked=(not g2c.allowed),
                reason="" if g2c.allowed else g2c.reason,
                prior_error=exc.sanitized,
            )
            return

        run_state.timings["execute_ms"] = round((time.monotonic() - exec_started) * 1000, 1)
        descriptor = chart_descriptor(result["columns"], result["rows"])
        yield _event(
            "columns",
            columns=result["columns"],
            truncated=result["truncated"],
            backend_pid=result["backend_pid"],
        )
        rows = [json.loads(json.dumps(r, default=str)) for r in result["rows"]]
        for i in range(0, len(rows), 100):
            yield _event("rows", rows=rows[i : i + 100])
            await asyncio.sleep(0)
        yield _event(
            "chart",
            descriptor=descriptor,
            title=chart_title(descriptor, run_state.question),
        )
        yield _event(
            "stats",
            wall_ms=result["wall_ms"],
            fetch_ms=result["fetch_ms"],
            row_count=len(rows),
            generate_ms=run_state.timings.get("generate_ms"),
        )
        run_state.set_phase("done")

    return StreamingResponse(stream(), media_type="text/event-stream", headers=SSE_HEADERS)


@router.post("/reject")
async def reject(body: RunRef, request: Request):
    state = request.app.state
    run_state = state.registry.get(body.run_id)
    if run_state is None:
        raise HTTPException(status_code=404, detail="unknown run")
    if run_state.phase != "awaiting_approval":
        raise HTTPException(status_code=409, detail=f"run is {run_state.phase}")
    run_state.set_phase("rejected")
    return {"rejected": True}


@router.post("/stop")
async def stop(body: RunRef, request: Request):
    state = request.app.state
    return await state.registry.stop(body.run_id)


@router.get("/status")
async def status(request: Request):
    state = request.app.state
    snap = state.readiness.snapshot()
    return {
        **snap,
        "warehouse_tables": len(state.catalog) if state.catalog else 0,
        "model": state.settings.copilot_model,
        "provider": state.settings.copilot_llm_provider,
    }
