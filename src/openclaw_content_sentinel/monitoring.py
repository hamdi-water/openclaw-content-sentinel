"""
Phase 21: SRE Monitoring & Prometheus Telemetry.

===========================================================

This module provides production-grade observability for the sentinel.
It uses 'prometheus_client' for standardized metric collection and
an internal file-based exporter to 'metrics.prom'.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

try:
    from prometheus_client import CollectorRegistry, Counter, Histogram, write_to_textfile

    HAS_PROMETHEUS = True
except ImportError:
    HAS_PROMETHEUS = False
    CollectorRegistry = Any  # type: ignore[misc, assignment]
    Counter = Any  # type: ignore[misc, assignment]
    Histogram = Any  # type: ignore[misc, assignment]
    write_to_textfile = Any  # type: ignore[assignment]


class MonitoringManager:
    _instance: MonitoringManager | None = None

    def __new__(cls, *args: Any, **kwargs: Any) -> MonitoringManager:
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialized = False
        return cls._instance

    def __init__(self, metrics_path: str | None = None) -> None:
        if getattr(self, "_initialized", False):
            if metrics_path:
                self.metrics_path = Path(metrics_path)
            return
        self.metrics_path = (
            Path(metrics_path) if metrics_path else Path("workspace-data/metrics.prom")
        )
        self.registry = CollectorRegistry() if HAS_PROMETHEUS else None

        if self.registry:
            # --- Workflow Metrics ---
            self.total_runs = Counter(
                "sentinel_runs_total",
                "Total number of pipeline executions",
                ["status", "trigger"],
                registry=self.registry,
            )
            self.pipeline_latency = Histogram(
                "sentinel_pipeline_duration_seconds",
                "Time taken to complete a full run",
                registry=self.registry,
            )

            # --- RAG Metrics ---
            self.rag_groundedness_failures = Counter(
                "sentinel_rag_groundedness_failures_total",
                "Total number of groundedness check failures",
                registry=self.registry,
            )
            self.rag_retrieval_hits = Counter(
                "sentinel_rag_hits_total", "Total successful RAG retrievals", registry=self.registry
            )

            # --- Browser / Research Metrics ---
            self.scraping_errors = Counter(
                "sentinel_scraping_errors_total",
                "Total browser or HTTP scraping failures",
                ["source", "error_type"],
                registry=self.registry,
            )

            # --- SRE Incidents ---
            self.incidents = Counter(
                "sentinel_incidents_total",
                "Total unexpected system incidents",
                ["severity"],
                registry=self.registry,
            )

        self._initialized = True

    def toggle_run(self, status: str, trigger: str = "manual") -> None:
        if self.registry:
            self.total_runs.labels(status=status, trigger=trigger).inc()

    def record_latency(self, seconds: float) -> None:
        if self.registry:
            self.pipeline_latency.observe(seconds)

    def record_rag_failure(self) -> None:
        if self.registry:
            self.rag_groundedness_failures.inc()

    def record_rag_hit(self) -> None:
        if self.registry:
            self.rag_retrieval_hits.inc()

    def record_scraping_error(self, source: str, error_type: str) -> None:
        if self.registry:
            self.scraping_errors.labels(source=source, error_type=error_type).inc()

    def record_incident(self, severity: str = "error") -> None:
        if self.registry:
            self.incidents.labels(severity=severity).inc()

    def export(self) -> None:
        """Export current metrics to text file for Prometheus scraping."""
        if self.registry and HAS_PROMETHEUS:
            self.metrics_path.parent.mkdir(parents=True, exist_ok=True)
            write_to_textfile(str(self.metrics_path), self.registry)


# Global singleton
monitor = MonitoringManager()
