from __future__ import annotations

import structlog

logger = structlog.get_logger(__name__)


def setup_otel(endpoint: str, headers: str) -> None:
    """Optional OTLP export, off by default.

    Same contract as the chassis repos: blank endpoint means no tracing
    pipeline at all, not a broken one. When set, spans reach the collector the
    same way ShipGate's do; Grafana's endpoint additionally wants /v1/traces
    appended and its auth header percent-decoded before it gets here.
    """
    if not endpoint:
        return

    from opentelemetry import trace
    from opentelemetry.sdk.resources import Resource
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import BatchSpanProcessor

    resource = Resource.create({"service.name": "copilot"})
    provider = TracerProvider(resource=resource)

    try:
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter

        exporter = OTLPSpanExporter(
            endpoint=endpoint,
            headers=_parse_headers(headers),
        )
        provider.add_span_processor(BatchSpanProcessor(exporter))
        trace.set_tracer_provider(provider)
        logger.info("otel.enabled", endpoint=endpoint)
    except Exception as exc:  # noqa: BLE001 - tracing must never take the app down
        logger.warning("otel.disabled", error=str(exc))


def _parse_headers(raw: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for pair in raw.split(","):
        if "=" in pair:
            key, value = pair.split("=", 1)
            out[key.strip()] = value.strip()
    return out


def tracer(name: str = "copilot"):
    from opentelemetry import trace

    return trace.get_tracer(name)


__all__ = ["setup_otel", "tracer"]
