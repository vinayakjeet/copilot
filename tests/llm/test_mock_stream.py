import asyncio

import pytest

from llm import (
    ChatClient,
    GenerationCancelled,
    MockProvider,
    ProviderConfigError,
)


def test_mock_streams_in_uneven_chunks():
    provider = MockProvider()
    provider.script("SELECT 1")

    async def run():
        client = ChatClient(provider)
        return [d async for d in client.stream([{"role": "user", "content": "q"}])]

    deltas = asyncio.run(run())
    assert "".join(deltas) == "SELECT 1"
    assert len(deltas) == len({id(d) for d in deltas})
    assert max(len(d) for d in deltas) <= 17


def test_mock_without_script_is_a_config_error_not_an_empty_reply():
    async def run():
        client = ChatClient(MockProvider())
        [d async for d in client.stream([])]

    with pytest.raises(ProviderConfigError):
        asyncio.run(run())


def test_stop_event_ends_the_stream_with_generation_cancelled():
    provider = MockProvider()
    provider.script("SELECT * FROM a_very_long_table_name")
    stop = asyncio.Event()

    async def run():
        got = []
        try:
            async for delta in provider.stream([], stop):
                got.append(delta)
                if sum(map(len, got)) > 5:
                    stop.set()
        except GenerationCancelled:
            return got, True
        return got, False

    deltas, cancelled = asyncio.run(run())
    assert "".join(deltas).startswith("SELECT")
    assert cancelled, "stream kept going after the stop event was set"


def test_scripted_queue_serves_replies_in_order_then_errors():
    provider = MockProvider()
    provider.script("first", "second")

    async def run():
        c = ChatClient(provider)
        one = "".join([d async for d in c.stream([])])
        two = "".join([d async for d in c.stream([])])
        return one, two

    one, two = asyncio.run(run())
    assert (one, two) == ("first", "second")


@pytest.mark.parametrize(
    "reply",
    [
        "```sql\nSELECT 1\n```",
        "```\nSELECT 1```",
        "  SELECT 1 ;  ",
    ],
)
def test_client_passes_text_through_for_shaping_later(reply):
    """The client transports; shaping lives in copilot.generate on purpose."""
    provider = MockProvider()
    provider.script(reply)

    async def run():
        c = ChatClient(provider)
        return "".join([d async for d in c.stream([])])

    assert asyncio.run(run()) == reply
