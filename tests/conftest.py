from __future__ import annotations

import asyncio

import pytest

from copilot.config import reset_settings


@pytest.fixture(autouse=True)
def _clean_settings(monkeypatch):
    monkeypatch.setenv("COPILOT_LLM_PROVIDER", "mock")
    monkeypatch.delenv("COPILOT_RO_URL", raising=False)
    monkeypatch.delenv("OTEL_EXPORTER_OTLP_ENDPOINT", raising=False)
    reset_settings()
    yield
    reset_settings()


class FakeAnalyst:
    """Stands in for the real pool so API tests never touch Postgres."""

    def __init__(self, result: dict | None = None, fail: Exception | None = None):
        self.result = result or {
            "columns": ["city_name", "orders"],
            "rows": [["Bengaluru", 120], ["Mumbai", 98], ["Delhi", 77]],
            "truncated": False,
            "wall_ms": 12.0,
            "fetch_ms": 9.0,
            "backend_pid": 4242,
        }
        self.fail = fail
        self.executed: list[str] = []
        self.cancel_calls: list[str] = []

    async def warm(self) -> float:
        return 1.0

    async def close(self) -> None:
        return None

    async def execute(self, run_id: str, statement: str) -> dict:
        self.executed.append(statement)
        if self.fail is not None:
            raise self.fail
        await asyncio.sleep(0)
        return self.result

    async def cancel(self, run_id: str) -> bool:
        self.cancel_calls.append(run_id)
        return True


class ScriptedProvider:
    """Paces a canned reply out in chunks; honours the stop event."""

    name = "mock"

    def __init__(self, reply: str, delay: float = 0.0, chunk: int = 6) -> None:
        self.reply = reply
        self.delay = delay
        self.chunk = chunk

    async def stream(self, messages, stop: asyncio.Event):
        from llm.types import GenerationCancelled

        for i in range(0, len(self.reply), self.chunk):
            if stop.is_set():
                raise GenerationCancelled("stop requested")
            await asyncio.sleep(self.delay)
            yield self.reply[i : i + self.chunk]

    async def complete(self, messages, stop=None):
        chunks = []
        async for c in self.stream(messages, stop or asyncio.Event()):
            chunks.append(c)
        from llm.types import ChatResponse

        return ChatResponse(text="".join(chunks), provider=self.name, model="shim-1")
