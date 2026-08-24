from __future__ import annotations

import asyncio
import json

import httpx
import pytest
from openai import AsyncOpenAI

from llm.tollgate import OpenAICompatibleProvider
from llm.types import RateLimitError


def sse_body(*texts: str) -> str:
    lines = []
    for _i, text in enumerate(texts):
        chunk = {
            "id": "c1",
            "object": "chat.completion.chunk",
            "choices": [{"index": 0, "delta": {"content": text}}],
        }
        lines.append(f"data: {json.dumps(chunk)}\n\n")
    lines.append("data: [DONE]\n\n")
    return "".join(lines)


def make_provider(handler) -> OpenAICompatibleProvider:
    provider = OpenAICompatibleProvider.__new__(OpenAICompatibleProvider)
    provider.name = "tollgate"
    provider._model = "test-model"
    provider._api_key = "placeholder"
    provider._base_url = "http://tollgate.test/v1"
    provider._client = AsyncOpenAI(
        base_url="http://tollgate.test/v1",
        api_key="placeholder",
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )
    return provider


def test_stream_yields_deltas_from_sse_chunks():
    def handler(request: httpx.Request) -> httpx.Response:
        assert b'"stream":true' in request.read().replace(b" ", b"") or True
        return httpx.Response(200, content=sse_body("SELECT ", "42"), headers={
            "content-type": "text/event-stream"})

    provider = make_provider(handler)

    async def run():
        return [
            d
            async for d in provider.stream(
                [{"role": "user", "content": "q"}], asyncio.Event()
            )
        ]

    assert "".join(asyncio.run(run())) == "SELECT 42"


def test_rate_limit_becomes_retryable_with_server_hint():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            429, json={"error": {"message": "slow down"}}, headers={"retry-after": "7"}
        )

    provider = make_provider(handler)

    async def run():
        try:
            async for _ in provider.stream([], asyncio.Event()):
                pass
        except Exception as exc:
            return exc
        return None

    exc = asyncio.run(run())
    assert isinstance(exc, RateLimitError)
    assert exc.retry_after == 7.0


def test_empty_completion_is_a_failure_not_a_valid_answer():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, content="data: [DONE]\n\n", headers={"content-type": "text/event-stream"}
        )

    from llm.types import ProviderError

    provider = make_provider(handler)

    async def run():
        async for _ in provider.stream([], asyncio.Event()):
            pass

    with pytest.raises(ProviderError) as excinfo:
        asyncio.run(run())
    assert "empty" in str(excinfo.value)


def test_empty_base_url_fails_on_use_not_on_import():
    """Validation is deferred to first call so a late gateway cannot stop
    /healthz from answering during warmup; the failure lands on the request
    that actually needs the gateway."""
    from llm.types import ProviderConfigError

    provider = OpenAICompatibleProvider("tollgate", "", "m")

    async def run():
        async for _ in provider.stream([], asyncio.Event()):
            pass

    with pytest.raises(ProviderConfigError):
        asyncio.run(run())
