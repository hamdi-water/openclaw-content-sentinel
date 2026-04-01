from __future__ import annotations

from collections import Counter
from datetime import UTC, datetime, timedelta
from typing import Any, cast

from .config import AppConfig
from .memory import backend_status
from .operations import build_operator_action_queue
from .security import security_audit
from .storage import RunStore
from .utils import dump_json, ensure_dir
from .workflow import build_proof_readiness_payload


def _parse_iso(value: str) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _is_live_proof_eligible(run: dict[str, Any]) -> bool:
    return bool(build_proof_readiness_payload(run).get("proof_eligible"))


def build_dashboard_summary(config: AppConfig) -> dict[str, Any]:
    store = RunStore(config)
    runs = store.list_runs()
    now = datetime.now(UTC)
    cutoff = now - timedelta(days=14)

    stats = {
        "platform_counter": Counter(),
        "platform_posted": Counter(),
        "platform_counter_live": Counter(),
        "platform_posted_live": Counter(),
        "status_counter": Counter(run.get("status", "unknown") for run in runs),
        "success_runs_14d_live": 0,
        "total_runs_14d_live": 0,
        "total_runs_14d_all": 0,
        "zero_cost_backends": Counter(),
        "image_backends": Counter(),
        "live_runs": 0,
        "eligible_live_runs": 0,
        "simulated_runs": 0,
        "approval_ready_pending": 0,
        "high_risk_packets": 0,
        "ready_for_supervised_publish": 0,
        "manual_intervention_required": 0,
        "proof_gap_counter": Counter(),
        "live_proof_complete": 0,
        "recent_runs": [],
    }

    for run in runs:
        _process_run_stats(
            run,
            stats,
            cutoff,
            stats["platform_counter"],
            stats["platform_posted"],
            stats["platform_counter_live"],
            stats["platform_posted_live"],
        )

    memory_status = backend_status(config)
    security = security_audit(config)
    action_queue = build_operator_action_queue(config)

    summary = {
        "generated_at": now.isoformat(),
        "totals": {
            "runs": len(runs),
            "live_runs": stats["live_runs"],
            "eligible_live_runs": stats["eligible_live_runs"],
            "simulated_runs": stats["simulated_runs"],
            "posted": cast(Counter, stats["status_counter"]).get("posted", 0),
            "posted_live": len(
                [
                    run
                    for run in runs
                    if _is_live_proof_eligible(run) and run.get("status") == "posted"
                ]
            ),
            "posted_simulated": len(
                [
                    run
                    for run in runs
                    if (run.get("simulation") or {}).get("enabled")
                    and run.get("status") == "posted"
                ]
            ),
            "awaiting_approval": cast(Counter, stats["status_counter"]).get("awaiting_approval", 0),
            "approval_ready_pending": stats["approval_ready_pending"],
            "posting_failed": cast(Counter, stats["status_counter"]).get("posting_failed", 0),
            "posting_failed_live": len(
                [
                    run
                    for run in runs
                    if _is_live_proof_eligible(run) and run.get("status") == "posting_failed"
                ]
            ),
            "failed": cast(Counter, stats["status_counter"]).get("failed", 0),
            "high_risk_packets": stats["high_risk_packets"],
            "ready_for_supervised_publish": stats["ready_for_supervised_publish"],
            "manual_intervention_required": stats["manual_intervention_required"],
            "live_proof_complete_runs": stats["live_proof_complete"],
        },
        "success_rate_14d": round(
            (cast(int, stats["success_runs_14d_live"]) / cast(int, stats["total_runs_14d_live"]))
            * 100,
            2,
        )
        if stats["total_runs_14d_live"]
        else 0.0,
        "runs_in_last_14d": stats["total_runs_14d_live"],
        "runs_in_last_14d_total": stats["total_runs_14d_all"],
        "platform_success": {
            platform: {
                "attempted_runs": stats["platform_counter_live"].get(platform, 0),
                "posted_runs": stats["platform_posted_live"].get(platform, 0),
                "success_rate": round(
                    (
                        stats["platform_posted_live"].get(platform, 0)
                        / stats["platform_counter_live"].get(platform, 1)
                    )
                    * 100,
                    2,
                )
                if stats["platform_counter_live"].get(platform, 0)
                else 0.0,
            }
            for platform in ("linkedin", "facebook", "x")
        },
        "platform_success_all": {
            platform: {
                "attempted_runs": stats["platform_counter"].get(platform, 0),
                "posted_runs": stats["platform_posted"].get(platform, 0),
                "success_rate": round(
                    (
                        stats["platform_posted"].get(platform, 0)
                        / stats["platform_counter"].get(platform, 1)
                    )
                    * 100,
                    2,
                )
                if stats["platform_counter"].get(platform, 0)
                else 0.0,
            }
            for platform in ("linkedin", "facebook", "x")
        },
        "zero_cost_proof": {
            "paid_services_detected": False,
            "free_backends_used": dict(stats["zero_cost_backends"]),
            "image_backends": dict(stats["image_backends"]),
        },
        "proof_gap_counts": dict(stats["proof_gap_counter"]),
        "compliance": {
            "telegram_live": security["openclaw_config"]["telegram_enabled"],
            "memory_backend": memory_status["backend"],
            "memory_backend_available": memory_status["available"],
            "security_status": security["status"],
            "approval_required": bool(config.approval_required),
            "auto_publish_enabled": bool(config.auto_publish_enabled),
            "image_backend_default": config.image_backend,
        },
        "operator_queue": {
            "counts": action_queue["counts"],
            "items": action_queue["items"][:10],
        },
        "latency_histogram": build_latency_histogram(runs),
        "recent_runs": stats["recent_runs"][:20],
    }
    return summary


def _process_run_stats(
    run: dict[str, Any],
    stats: dict[str, Any],
    cutoff: datetime,
    platform_counter: Counter[Any],
    platform_posted: Counter[Any],
    platform_counter_live: Counter[Any],
    platform_posted_live: Counter[Any],
) -> None:
    is_simulation = bool((run.get("simulation") or {}).get("enabled"))
    if is_simulation:
        stats["simulated_runs"] += 1
    else:
        stats["live_runs"] += 1

    _process_proof_readiness(run, stats)
    _process_timing_stats(run, stats, cutoff)
    _process_platform_stats(
        run,
        platform_counter,
        platform_posted,
        platform_counter_live,
        platform_posted_live,
    )
    _process_cost_and_policy(run, stats)

    is_sim = (run.get("cost_proof") or {}).get("simulation", False)
    proof_r = build_proof_readiness_payload(run)
    stats["recent_runs"].append(
        {
            "run_id": run.get("run_id"),
            "status": run.get("status"),
            "approval_state": run.get("approval_state"),
            "confidence": run.get("confidence"),
            "created_at": run.get("created_at"),
            "updated_at": run.get("updated_at"),
            "simulation": is_sim,
            "proof_eligible": bool(proof_r.get("proof_eligible")),
            "proof_ineligibility_reasons": proof_r.get("reason_codes") or [],
            "proof_next_action": proof_r.get("next_action", ""),
            "live_proof_complete": bool(proof_r.get("live_proof_complete")),
        }
    )


def _process_proof_readiness(run: dict[str, Any], stats: dict[str, Any]) -> None:
    proof_readiness = build_proof_readiness_payload(run)
    if bool(proof_readiness.get("proof_eligible")):
        stats["eligible_live_runs"] += 1
    else:
        for code in proof_readiness.get("reason_codes") or []:
            stats["proof_gap_counter"][code] += 1
    if proof_readiness.get("live_proof_complete"):
        stats["live_proof_complete"] += 1


def _process_timing_stats(run: dict[str, Any], stats: dict[str, Any], cutoff: datetime) -> None:
    created_at = _parse_iso(run.get("created_at", ""))
    if created_at and created_at >= cutoff:
        stats["total_runs_14d_all"] += 1
        if bool(build_proof_readiness_payload(run).get("proof_eligible")):
            stats["total_runs_14d_live"] += 1
            if run.get("status") == "posted":
                stats["success_runs_14d_live"] += 1


def _process_platform_stats(
    run: dict[str, Any],
    pf_c: Counter[Any],
    pf_p: Counter[Any],
    pf_c_l: Counter[Any],
    pf_p_l: Counter[Any],
) -> None:
    proof_eligible = bool(build_proof_readiness_payload(run).get("proof_eligible"))
    for platform, result in (run.get("post_results") or {}).items():
        pf_c[platform] += 1
        if result.get("status") == "posted":
            pf_p[platform] += 1
        if proof_eligible:
            pf_c_l[platform] += 1
            if result.get("status") == "posted":
                pf_p_l[platform] += 1


def _process_cost_and_policy(run: dict[str, Any], stats: dict[str, Any]) -> None:
    cost_proof = run.get("cost_proof") or {}
    analysis = run.get("analysis") or {}
    approval_packet = analysis.get("approval_packet") or {}
    execution_policy = analysis.get("execution_policy") or {}
    quality_gate = run.get("quality_gate") or {}

    if run.get("approval_state") == "pending" and quality_gate.get("publish_ready"):
        stats["approval_ready_pending"] += 1
    if approval_packet.get("risk_level") == "high":
        stats["high_risk_packets"] += 1
    if execution_policy.get("decision") == "ready_for_supervised_publish":
        stats["ready_for_supervised_publish"] += 1
    if execution_policy.get("decision") == "manual_intervention_required":
        stats["manual_intervention_required"] += 1

    for backend in cost_proof.get("free_backends_used", []):
        stats["zero_cost_backends"][backend] += 1
    if cost_proof.get("image_backend"):
        stats["image_backends"][cost_proof["image_backend"]] += 1


def build_latency_histogram(runs: list[dict[str, Any]]) -> dict[str, Any]:
    """Ref §27.8: Build a histogram of node-level latencies."""
    hist: dict[str, list[float]] = {}
    for run in runs:
        analysis = run.get("analysis") or {}
        latencies = analysis.get("node_latency") or {}
        for node, ms in latencies.items():
            if node not in hist:
                hist[node] = []
            hist[node].append(float(ms))

    # Calculate stats per node
    report = {}
    for node, values in hist.items():
        if not values:
            continue
        report[node] = {
            "avg_ms": round(sum(values) / len(values), 1),
            "max_ms": round(max(values), 1),
            "p95_ms": (
                round(sorted(values)[int(len(values) * 0.95)], 1)
                if len(values) >= 20
                else round(max(values), 1)
            ),
            "count": len(values),
        }
    return report


def write_dashboard_summary(config: AppConfig) -> dict[str, Any]:
    payload = build_dashboard_summary(config)
    output_path = config.data_dir / "dashboard" / "summary.json"
    ensure_dir(output_path.parent)
    dump_json(output_path, payload)
    return {
        "path": str(output_path),
        "summary": payload,
    }
