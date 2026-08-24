from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncIterator
from typing import Protocol

import structlog

from llm.types import (
    ChatMessage,
    ChatResponse,
    GenerationCancelled,
    ProviderConfigError,
    ProviderError,
    RateLimitError,
)

logger = structlog.get_logger(__name__)


class StreamingProvider(Protocol):
    name: str

    async def stream(
        self, messages: list[ChatMessage], stop: asyncio.Event
    ) -> AsyncIterator[str]: ...

    async def complete(
        self, messages: list[ChatMessage], stop: asyncio.Event | None = None
    ) -> ChatResponse: ...


class ChatClient:
    """Dispatches to the configured provider with retry around the whole stream.

    A stream that fails before its first delta is retried from the start, which
    is correct for SQL generation: a half statement is worthless, so there is
    nothing to resume. Once deltas have been emitted a retry would duplicate
    them into the client's buffer, so a mid-stream failure is raised instead.
    The stop event is shared across attempts, so a user who pressed stop during
    a failing attempt never sees a second attempt begin.
    """

    def __init__(self, provider: StreamingProvider, max_retry_attempts: int = 3) -> None:
        self._provider = provider
        self._max_retry_attempts = max_retry_attempts
        self._cooldown_until = 0.0

    async def stream(
        self, messages: list[ChatMessage], stop: asyncio.Event | None = None
    ) -> AsyncIterator[str]:
        stop = stop or asyncio.Event()
        for attempt in range(1, self._max_retry_attempts + 1):
            if self._cooldown_until > time.monotonic() and not stop.is_set():
                await asyncio.sleep(max(0.0, self._cooldown_until - time.monotonic()))
            started = time.monotonic()
            emitted = False
            try:
                async for delta in self._guarded(messages, stop):
                    emitted = True
                    yield delta
                logger.info(
                    "llm.stream",
                    provider=self._provider.name,
                    latency_ms=(time.monotonic() - started) * 1000,
                )
                return
            except ProviderError as exc:
                if isinstance(exc, RateLimitError) and exc.retry_after:
                    self._cooldown_until = time.monotonic() + exc.retry_after
                if emitted or attempt == self._max_retry_attempts or stop.is_set():
                    raise
                logger.warning("llm.retry", provider=self._provider.name, error=str(exc))
        raise ProviderConfigError("retry loop exhausted without raising")  # pragma: no cover

    async def _guarded(
        self, messages: list[ChatMessage], stop: asyncio.Event
    ) -> AsyncIterator[str]:
        async for delta in self._provider.stream(messages, stop):
            if stop.is_set():
                raise GenerationCancelled("stop requested")
            yield delta

    def _stream_once(self, messages: list[ChatMessage], stop: asyncio.Event):
        return self._provider.stream(messages, stop)

    async def complete(
        self, messages: list[ChatMessage], stop: asyncio.Event | None = None
    ) -> ChatResponse:
        chunks: list[str] = []
        async for delta in self.stream(messages, stop):
            chunks.append(delta)
        return ChatResponse(text="".join(chunks), provider=self._provider.name, model="client")
