"""Durable assistant outcome metrics; worker restarts cannot reset these totals."""

from __future__ import annotations

from prometheus_client import CollectorRegistry, generate_latest
from prometheus_client.core import CounterMetricFamily, GaugeMetricFamily
from sqlalchemy import func, select
from sqlalchemy.engine import Engine
from sqlalchemy.exc import SQLAlchemyError

from app.db import schema as s


def render(engine: Engine) -> bytes:
    """Prometheus exposition of committed assistant outcomes, displays and
    availability, labelled by mode, profile, build and arm. Reads committed rows only,
    so a metric never reports an uncommitted call."""
    outcomes = CounterMetricFamily(
        "investigators_assistant_calls",
        "Committed assistant outcomes",
        labels=["mode", "profile", "build", "arm", "status"],
    )
    deliveries = CounterMetricFamily(
        "investigators_assistant_deliveries",
        "Committed assistant displays",
        labels=["mode", "profile", "build", "arm", "fallback"],
    )
    available = GaugeMetricFamily(
        "investigators_assistant_metrics_available", "Database metrics available"
    )
    try:
        with engine.connect() as conn:
            dims = [
                s.session.c.mode,
                s.session.c.manifest_id,
                s.session.c.build_source_sha256,
                s.session.c.arm,
            ]
            call_query = (
                select(*dims, s.model_call.c.status, func.count())
                .select_from(
                    s.model_call.join(
                        s.session, s.session.c.session_id == s.model_call.c.session_id
                    ).join(
                        s.knowledge_snapshot,
                        (s.knowledge_snapshot.c.session_id == s.model_call.c.session_id)
                        & (s.knowledge_snapshot.c.snapshot_id == s.model_call.c.snapshot_id),
                    )
                )
                .where(s.knowledge_snapshot.c.purpose == "assistant_context")
                .group_by(*dims, s.model_call.c.status)
            )
            for row in conn.execute(call_query):
                outcomes.add_metric([str(v or "unassigned") for v in row[:-1]], row[-1])
            display_query = (
                select(*dims, s.displayed_output.c.fallback, func.count())
                .select_from(
                    s.displayed_output.join(
                        s.session, s.session.c.session_id == s.displayed_output.c.session_id
                    ).join(
                        s.message,
                        (s.message.c.session_id == s.displayed_output.c.session_id)
                        & (s.message.c.message_id == s.displayed_output.c.message_id),
                    )
                )
                .where(s.message.c.actor == "assistant")
                .group_by(*dims, s.displayed_output.c.fallback)
            )
            for row in conn.execute(display_query):
                deliveries.add_metric([str(v or "unassigned") for v in row[:-1]], row[-1])
        available.add_metric([], 1)
    except SQLAlchemyError:
        available.add_metric([], 0)
    registry = CollectorRegistry()
    registry.register(
        type(
            "AssistantCollector",
            (),
            {"collect": lambda _self: iter((outcomes, deliveries, available))},
        )()
    )
    return generate_latest(registry)
