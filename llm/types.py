from __future__ import annotations

from pydantic import BaseModel


class ChatMessage(BaseModel):
    role: str
    content: str


class ChatResponse(BaseModel):
    text: str
    provider: str
    model: str
    tokens_in: int | None = None
    tokens_out: int | None = None
    latency_ms: float = 0.0


class ProviderError(Exception):
    """Transient provider failure, safe to retry."""


class RateLimitError(ProviderError):
    def __init__(self, message: str, retry_after: float | None = None) -> None:
        super().__init__(message)
        self.retry_after = retry_after


class ProviderClientError(Exception):
    """Non-retryable failure: bad auth, bad request, unknown model."""


class ProviderConfigError(Exception):
    """Provider misconfigured: unknown name or missing URL."""


class GenerationCancelled(Exception):
    """The caller asked for the stream to stop and the stream obliged.

    Distinct from a provider failure so a user-initiated stop is never retried
    and never surfaces as an error to the client.
    """
