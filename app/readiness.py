from __future__ import annotations

import time

import structlog

logger = structlog.get_logger(__name__)


class Readiness:
    """Cold-start state, reported stage by stage.

    Render's free tier suspends idle services; the next request pays a boot
    measured in tens of seconds. A spinner hides that. This reports which named
    stage is running and how long the process has been up, so the UI can show
    an honest checklist while nothing is ready yet. Stages are recorded but
    never block startup: the app serves /healthz and /api/status before the
    warehouse connection exists.
    """

    STAGES = ("boot", "warehouse", "schema")

    def __init__(self) -> None:
        self.started_at = time.monotonic()
        self._done: dict[str, float] = {}
        self._current: str | None = "boot"

    def begin(self, stage: str) -> None:
        if stage not in self.STAGES:
            raise ValueError(f"unknown stage {stage!r}")
        self._current = stage

    def complete(self, stage: str, detail_ms: float | None = None) -> None:
        if stage not in self.STAGES:
            raise ValueError(f"unknown stage {stage!r}")
        self._done[stage] = round(detail_ms if detail_ms is not None else 0.0, 1)
        remaining = [s for s in self.STAGES if s not in self._done]
        self._current = remaining[0] if remaining else None

    @property
    def ready(self) -> bool:
        return all(s in self._done for s in self.STAGES)

    def snapshot(self) -> dict:
        stages = {
            name: {
                "state": (
                    "done" if name in self._done
                    else "running" if name == self._current
                    else "waiting"
                ),
                "ms": self._done.get(name),
            }
            for name in self.STAGES
        }
        return {
            "ready": self.ready,
            "uptime_s": round(time.monotonic() - self.started_at, 1),
            "stages": stages,
        }
