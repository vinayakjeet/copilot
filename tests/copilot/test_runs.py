import asyncio

from copilot.runs import RunRegistry


def test_create_and_phase_transitions():
    registry = RunRegistry()
    state = registry.create("how many orders")
    assert state.phase == "generating"
    state.set_phase("awaiting_approval")
    state.set_phase("done")
    assert registry.get(state.id).phase == "done"


def test_unknown_phase_rejected():
    state = RunRegistry().create("q")
    try:
        state.set_phase("teleported")
    except ValueError:
        return
    raise AssertionError("expected ValueError")


async def _long_generation(state):
    for _ in range(100):
        if state.stop_event.is_set():
            raise asyncio.CancelledError()
        await asyncio.sleep(0.01)


async def test_stop_during_generation_cancels_task_and_marks_run():
    registry = RunRegistry()
    state = registry.create("q")
    state.generation_task = asyncio.create_task(_long_generation(state))
    await asyncio.sleep(0.03)

    report = await registry.stop(state.id)
    assert report["stopped"] is True
    assert report["where"] == "generation"
    assert state.phase == "cancelled"
    assert state.generation_task.cancelled()


async def test_stop_during_execution_calls_analyst_cancel():
    class FakeAnalyst:
        def __init__(self):
            self.calls = []

        async def cancel(self, run_id: str) -> bool:
            self.calls.append(run_id)
            return True

    registry = RunRegistry()
    state = registry.create("q")
    state.analyst = FakeAnalyst()
    state.set_phase("executing")

    report = await registry.stop(state.id)
    assert report["stopped"] is True
    assert report["via_pg_cancel_backend"] is True
    assert state.analyst.calls == [state.id]


async def test_stop_on_finished_run_reports_without_touching_it():
    registry = RunRegistry()
    state = registry.create("q")
    state.set_phase("done")
    report = await registry.stop(state.id)
    assert report["stopped"] is False
    assert "already" in report["reason"]
