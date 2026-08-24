from __future__ import annotations

from collections.abc import Callable
from typing import TypeVar

from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_random_exponential

from llm.types import ProviderError

T = TypeVar("T")


def retry_with_backoff(
    max_attempts: int = 3, min_wait: float = 1.0, max_wait: float = 20.0
) -> Callable[[Callable[..., T]], Callable[..., T]]:
    """Exponential backoff with jitter for transient failures only.

    GenerationCancelled is deliberately outside the retried set: a user stop is
    not a failure, and retrying it would restart work the user just killed.
    """
    return retry(
        retry=retry_if_exception_type(ProviderError),
        stop=stop_after_attempt(max_attempts),
        wait=wait_random_exponential(multiplier=min_wait, max=max_wait),
        reraise=True,
    )
