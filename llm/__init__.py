"""Provider-agnostic chat access for Copilot.

Production traffic routes through Tollgate, which owns the provider keys and
meters every call; a direct-Groq route exists because the gateway was observed
returning empty streams for multi-KB prompts that the identical upstream
answered fine. The mock provider keeps the gates green with no network and no
credentials. Import this package first: it loads .env before anything reads
os.environ.
"""

from llm.client import ChatClient
from llm.mock import MockProvider
from llm.tollgate import OpenAICompatibleProvider, make_provider
from llm.types import (
    ChatMessage,
    ChatResponse,
    GenerationCancelled,
    ProviderClientError,
    ProviderConfigError,
    ProviderError,
    RateLimitError,
)

# Historical name kept so existing call sites read naturally.
TollgateProvider = OpenAICompatibleProvider

__all__ = [
    "ChatClient",
    "ChatMessage",
    "ChatResponse",
    "GenerationCancelled",
    "MockProvider",
    "OpenAICompatibleProvider",
    "ProviderClientError",
    "ProviderConfigError",
    "ProviderError",
    "RateLimitError",
    "TollgateProvider",
    "make_provider",
]
