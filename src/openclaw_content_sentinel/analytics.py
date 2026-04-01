from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

from .config import AppConfig
from .storage import RunStore

logger = logging.getLogger(__name__)


def calculate_competitor_delta(config: AppConfig, run_id: str) -> float:
    """Compare a run with the current competitor-overlap baseline."""
    store = RunStore(config)
    run = store.load_run(run_id)
    current_overlap = float((run.get("analysis") or {}).get("competitor_overlap_score", 0.0))
    delta = round(1.0 - current_overlap, 3)
    logger.info("Calculated competitor delta for %s: %s", run_id, delta)
    return delta


def get_trend_momentum(config: AppConfig, keyword: str) -> float:
    """Estimate momentum for a keyword from recent runs."""
    store = RunStore(config)
    recent_runs = store.list_runs(limit=20)
    hits = sum(
        1
        for run in recent_runs
        if keyword.lower() in [item.lower() for item in run.get("keywords", [])]
    )
    momentum = round(hits / 20.0, 2) if recent_runs else 0.0
    logger.info("Trend momentum for %s: %s", keyword, momentum)
    return momentum


def update_run_analytics(config: AppConfig, run_id: str) -> None:
    """Update comparative analytics fields for one run."""
    store = RunStore(config)
    run = store.load_run(run_id)
    delta = calculate_competitor_delta(config, run_id)

    analysis = dict(run.get("analysis") or {})
    analysis["competitor_delta"] = delta
    keywords = run.get("keywords", [])
    if keywords:
        top_keyword = keywords[0]
        analysis["keyword_affinity"] = {top_keyword: get_trend_momentum(config, top_keyword)}

    run["analysis"] = analysis
    store.save_run(run)


class ComparativeScorer:
    """Provide a simple competitor comparison score for the gap tracker."""

    def __init__(self, config: AppConfig) -> None:
        self.config = config
        self.store = RunStore(config)

    def compare_to_competitor(
        self, run_id: str, competitor_baseline: dict[str, Any]
    ) -> dict[str, Any]:
        """Compare one run to a supplied competitor baseline."""
        run = self.store.load_run(run_id)
        current_overlap = float((run.get("analysis") or {}).get("competitor_overlap_score", 0.0))
        baseline_overlap = float(competitor_baseline.get("overlap_score", 0.5))
        delta = round(baseline_overlap - current_overlap, 3)
        return {
            "delta": delta,
            "status": "superior" if delta > 0.1 else "aligned",
            "timestamp": datetime.now(UTC).isoformat(),
        }
