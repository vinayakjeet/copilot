from __future__ import annotations

import asyncio
import os
from collections.abc import AsyncIterator

import structlog
from openai import APIConnectionError as OpenAIConnectionError
from openai import APIError, AsyncOpenAI
from openai import InternalServerError as OpenAIServerError
from openai import RateLimitError as OpenAIRateLimit

from llm.types import (
    ChatMessage,
    ChatResponse,
    GenerationCancelled,
    ProviderClientError,
    ProviderConfigError,
    ProviderError,
    RateLimitError,
)

logger = structlog.get_logger(__name__)

PLACEHOLDER_KEY = "copilot-holds-no-provider-key"


class OpenAICompatibleProvider:
    """Streaming chat against any OpenAI-compatible base URL.

    One implementation serves both routes this project uses: through Tollgate
    (which holds the provider keys and meters the call) and direct to a
    provider when the gateway route is unavailable. Which one runs is config,
    not code.
    """

    def __init__(self, name: str, base_url: str, model: str, api_key: str | None = None) -> None:
        self.name = name
        self._model = model
        self._api_key = api_key
        self._base_url = base_url
        self._client = None

    def _ensure_client(self) -> AsyncOpenAI:
        if self._client is None:
            if not self._base_url:
                raise ProviderConfigError(
                    f"provider '{self.name}' has no base URL configured"
                )
            self._client = AsyncOpenAI(
                base_url=self._base_url,
                api_key=self._api_key or PLACEHOLDER_KEY,
            )
        return self._client

    @staticmethod
    def _payload(messages: list[ChatMessage]) -> list[dict]:
        out = []
        for m in messages:
            if isinstance(m, ChatMessage):
                out.append(m.model_dump())
            else:
                out.append(dict(m))
        return out

    async def stream(self, messages: list[ChatMessage], stop: asyncio.Event) -> AsyncIterator[str]:
        client = self._ensure_client()
        try:
            response = await client.chat.completions.create(
                model=self._model,
                messages=self._payload(messages),
                temperature=0,
                stream=True,
            )
        except (OpenAIConnectionError, OpenAIRateLimit, OpenAIServerError) as exc:
            raise self._translate(exc) from exc
        except APIError as exc:
            raise ProviderClientError(str(exc)) from exc

        try:
            produced = False
            async for chunk in response:
                if stop.is_set():
                    # Closing the iterator tears down the underlying HTTP
                    # response; the upstream sees the disconnect instead of
                    # paying for tokens nobody will read.
                    await response.close()
                    raise GenerationCancelled("stop requested")
                if chunk.choices and chunk.choices[0].delta.content:
                    produced = True
                    yield chunk.choices[0].delta.content
        except (OpenAIConnectionError, OpenAIRateLimit, OpenAIServerError) as exc:
            raise self._translate(exc) from exc

        if not produced:
            # A zero-byte completion is a failure (provider exhaustion caught
            # mid-window, upstream hiccup), never a valid answer here.
            raise ProviderError("empty completion from provider")

    async def complete(
        self, messages: list[ChatMessage], stop: asyncio.Event | None = None
    ) -> ChatResponse:
        chunks: list[str] = []
        usage_in: int | None = None
        usage_out: int | None = None
        client = self._ensure_client()
        try:
            response = await client.chat.completions.create(
                model=self._model,
                messages=self._payload(messages),
                temperature=0,
                stream=True,
                stream_options={"include_usage": True},
            )
            async for chunk in response:
                if stop is not None and stop.is_set():
                    await response.close()
                    raise GenerationCancelled("stop requested")
                if chunk.choices and chunk.choices[0].delta.content:
                    chunks.append(chunk.choices[0].delta.content)
                if getattr(chunk, "usage", None):
                    usage_in = chunk.usage.prompt_tokens
                    usage_out = chunk.usage.completion_tokens
        except (OpenAIConnectionError, OpenAIRateLimit, OpenAIServerError) as exc:
            raise self._translate(exc) from exc
        except APIError as exc:
            raise ProviderClientError(str(exc)) from exc

        if not chunks:
            raise ProviderError("empty completion from provider")

        return ChatResponse(
            text="".join(chunks),
            provider=self.name,
            model=self._model,
            tokens_in=usage_in,
            tokens_out=usage_out,
        )

    @staticmethod
    def _translate(exc: Exception) -> Exception:
        if isinstance(exc, OpenAIRateLimit):
            retry_after = getattr(exc.response, "headers", {}).get("retry-after")
            seconds = None
            if retry_after:
                try:
                    seconds = float(retry_after)
                except ValueError:
                    seconds = None
            return RateLimitError(str(exc), retry_after=seconds)
        if isinstance(exc, (OpenAIConnectionError, OpenAIServerError)):
            return ProviderError(str(exc))
        return ProviderClientError(str(exc))


def make_provider(provider_name: str, settings) -> OpenAICompatibleProvider | object:
    """Build the configured route.

    tollgate  : the portfolio gateway, which owns provider keys and metering.
    groq      : direct to Groq with GROQ_API_KEY; the escape hatch after the
                gateway was observed returning empty streams for multi-KB
                prompts while the identical prompt worked straight upstream.
    mock      : scripted replies, no network, what CI runs on.
    """
    if provider_name == "tollgate":
        return OpenAICompatibleProvider(
            "tollgate", settings.tollgate_url, settings.copilot_model
        )
    if provider_name == "groq":
        key = os.environ.get("GROQ_API_KEY", "")
        model = settings.copilot_model
        if model.startswith("groq/"):
            model = model[len("groq/") :]
        return OpenAICompatibleProvider(
            "groq", "https://api.groq.com/openai/v1", model, api_key=key
        )
    from llm.mock import MockProvider

    return MockProvider()


