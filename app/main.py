from __future__ import annotations

import asyncio
import sys
from contextlib import asynccontextmanager
from pathlib import Path

import structlog
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

# psycopg's async mode needs a selector loop; Windows defaults to proactor.
# Set at import time rather than in __main__ so every launch path
# (`uvicorn app.main:app` included) gets it.
if sys.platform == "win32":
    _selector = getattr(asyncio, "WindowsSelectorEventLoopPolicy", None)
    if _selector is not None:
        asyncio.set_event_loop_policy(_selector())

from app.config import get_settings  # noqa: E402
from app.logging_config import configure_logging  # noqa: E402
from app.middleware import RequestContextMiddleware  # noqa: E402
from app.otel_bootstrap import setup_otel  # noqa: E402
from app.readiness import Readiness  # noqa: E402
from app.routers import api, health, ui  # noqa: E402

logger = structlog.get_logger(__name__)

STATIC_DIR = Path(__file__).resolve().parent / "static"


async def _warmup(app: FastAPI) -> None:
    """Cold-start work, deliberately off the request path.

    Render free spins the whole process down; whoever arrives first watches the
    stages through /api/status instead of a spinner. The schema cache is what
    turns linking from 'query pg_catalog per question' into 'dict lookup'.
    """
    readiness = app.state.readiness
    settings = app.state.settings

    readiness.begin("warehouse")
    from copilot.analyst import Analyst

    analyst = Analyst(
        ro_url=settings.copilot_ro_url,
        statement_timeout_ms=settings.statement_timeout_ms,
        row_cap=settings.row_cap,
    )
    try:
        warm_ms = await analyst.warm()
        readiness.complete("warehouse", detail_ms=warm_ms)
    except Exception as exc:  # noqa: BLE001 - report, do not crash the boot
        logger.error("warmup.warehouse_failed", error=str(exc))
        readiness.begin("schema")
        readiness.complete("schema")
        return

    app.state.analyst = analyst
    readiness.begin("schema")
    from copilot.schema_linking import load_catalog

    catalog = await load_catalog(settings.copilot_ro_url)
    if not catalog:
        logger.error("warmup.catalog_empty")
    app.state.catalog = catalog
    readiness.complete("schema")
    logger.info("warmup.done", tables=len(catalog))


def create_app() -> FastAPI:
    settings = get_settings()
    configure_logging(settings.log_level)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        import asyncio

        app.state.readiness.complete("boot")
        task = asyncio.create_task(_warmup(app))
        yield
        task.cancel()
        if getattr(app.state, "analyst", None):
            await app.state.analyst.close()

    app = FastAPI(title="copilot", version="0.1.0", lifespan=lifespan)

    # Placeholder state so /healthz and /api/status answer during warmup;
    # _warmup replaces them as each stage lands.
    app.state.settings = settings
    app.state.readiness = Readiness()
    app.state.catalog = {}
    app.state.registry = None  # set below, cheap and synchronous
    app.state.analyst = None

    from copilot.runs import RunRegistry
    from llm import ChatClient, make_provider

    app.state.registry = RunRegistry()
    app.state.llm_client = ChatClient(
        make_provider(settings.copilot_llm_provider, settings),
        max_retry_attempts=settings.llm_max_retry_attempts,
    )

    app.add_middleware(RequestContextMiddleware)
    app.include_router(api.router)
    app.include_router(health.router)
    app.include_router(ui.router)
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

    setup_otel(settings.otel_exporter_otlp_endpoint, settings.otel_exporter_otlp_headers)

    return app


app = create_app()

if __name__ == "__main__":
    # uvicorn.run() forces Windows onto its proactor loop, which psycopg's
    # async mode cannot use. Driving Server.serve() through asyncio.run()
    # keeps the selector policy set above in charge. Render runs Linux, where
    # none of this matters.
    import os

    import uvicorn

    config = uvicorn.Config(
        "app.main:app",
        host="0.0.0.0",
        port=int(os.environ.get("PORT", "8000")),
        log_level="info",
    )
    server = uvicorn.Server(config)
    asyncio.run(server.serve())
