from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator

import structlog

from llm.types import (
    ChatMessage,
    ChatResponse,
    GenerationCancelled,
    ProviderConfigError,
)

logger = structlog.get_logger(__name__)

# Streams are cut into uneven chunks on purpose: a mock that yields one token
# per chunk hides ordering bugs that only show up when a delta carries several.
CHUNK_SIZES = (4, 11, 2, 17, 5)


class MockProvider:
    """Scripted stand-in for Tollgate. No network, no keys.

    Responses come from a queue set by the test or the demo seed script, which
    keeps every downstream layer (streaming, guards, correction loop) exercisable
    with deterministic output. An empty queue is a configuration error rather
    than an empty reply: silently returning nothing would look like a model that
    answered and corrupt whatever consumed it.
    """

    name = "mock"

    def __init__(self) -> None:
        self._scripted: list[str] = []
        self.calls: list[list[ChatMessage]] = []

    def script(self, *replies: str) -> None:
        self._scripted = list(replies)

    async def stream(self, messages: list[ChatMessage], stop: asyncio.Event) -> AsyncIterator[str]:
        if not self._scripted:
            raise ProviderConfigError(
                "mock provider has no scripted reply; call .script() first"
            )
        self.calls.append(list(messages))
        reply = self._scripted.pop(0)
        pos = 0
        for size in CHUNK_SIZES:
            if stop.is_set():
                raise GenerationCancelled("stop requested")
            chunk = reply[pos : pos + size]
            await asyncio.sleep(0)
            yield chunk
            pos += size
            if pos >= len(reply):
                break

    async def complete(
        self, messages: list[ChatMessage], stop: asyncio.Event | None = None
    ) -> ChatResponse:
        chunks: list[str] = []
        async for delta in self.stream(messages, stop or asyncio.Event()):
            chunks.append(delta)
        return ChatResponse(text="".join(chunks), provider=self.name, model="mock-1")
