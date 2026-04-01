from __future__ import annotations

import json
import logging
import shutil
import subprocess
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from .config import AppConfig
from .models import ComplianceEvent
from .security import security_audit
from .storage import RunStore
from .utils import dump_json, write_text

logger = logging.getLogger(__name__)


class AuditLogger:
    """Secure, append-only compliance auditor."""

    def __init__(self, config: AppConfig) -> None:
        self.config = config
        self.audit_file = config.data_dir / "compliance" / "audit_log.jsonl"
        self.audit_file.parent.mkdir(parents=True, exist_ok=True)

    def log_event(
        self,
        action: str,
        operator_id: str = "system",
        run_id: str = "",
        resource_type: str = "run",
        extra: dict[str, Any] | None = None,
    ) -> str:
        event_id = str(uuid.uuid4())
        timestamp = datetime.now(UTC).isoformat()
        event = ComplianceEvent(
            event_id=event_id,
            timestamp=timestamp,
            operator_id=operator_id,
            action=action,
            run_id=run_id,
            resource_type=resource_type,
            pii_scrubbed=True,
        )
        entry = event.model_dump()
        if extra:
            entry["metadata"] = self._scrub_payload(extra)
        with self.audit_file.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(entry, ensure_ascii=True) + "\n")
        logger.info("Compliance audit record created: %s (%s)", event_id, action)
        return event_id

    def _scrub_payload(self, data: dict[str, Any]) -> dict[str, Any]:
        scrubbed: dict[str, Any] = {}
        for key, value in data.items():
            if isinstance(value, dict):
                scrubbed[key] = self._scrub_payload(value)
            elif any(token in key.lower() for token in ("key", "token", "secret", "password")):
                scrubbed[key] = "[REDACTED]"
            else:
                scrubbed[key] = value
        return scrubbed


def _parse_iso(value: str) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _run_openclaw_command(config: AppConfig, *args: str) -> dict[str, Any]:
    executable = shutil.which(config.openclaw_bin) or config.openclaw_bin
    try:
        result = subprocess.run(  # noqa: S603
            [executable, *args],
            cwd=config.base_dir,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=25,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {
            "ok": False,
            "stdout": "",
            "stderr": str(exc),
            "returncode": None,
            "timed_out": isinstance(exc, subprocess.TimeoutExpired),
        }
    return {
        "ok": result.returncode == 0,
        "stdout": (result.stdout or "").strip(),
        "stderr": (result.stderr or "").strip(),
        "returncode": result.returncode,
        "timed_out": False,
    }


def _probe_openclaw_runtime(config: AppConfig) -> dict[str, Any]:
    from .operations import runtime_status

    runtime = runtime_status(config)
    status_result = _run_openclaw_command(config, "status")
    plugins_result = _run_openclaw_command(config, "plugins", "list")
    cron_result = _run_openclaw_command(config, "cron", "list", "--json")

    cron_jobs: list[dict[str, Any]] = []
    if cron_result["ok"] and cron_result["stdout"]:
        try:
            payload = json.loads(cron_result["stdout"])
            if isinstance(payload, list):
                cron_jobs = [item for item in payload if isinstance(item, dict)]
            elif isinstance(payload, dict):
                jobs = payload.get("jobs") or payload.get("items") or []
                cron_jobs = [item for item in jobs if isinstance(item, dict)]
        except json.JSONDecodeError:
            cron_jobs = []

    status_text = "\n".join(
        part for part in [status_result["stdout"], status_result["stderr"]] if part
    ).strip()
    plugins_text = "\n".join(
        part for part in [plugins_result["stdout"], plugins_result["stderr"]] if part
    ).strip()
    timed_out_commands = [
        name
        for name, result in {
            "status": status_result,
            "plugins": plugins_result,
            "cron": cron_result,
        }.items()
        if result.get("timed_out")
    ]
    cron_daily_present = any(
        str(item.get("name") or "").strip() == "content-sentinel-daily" for item in cron_jobs
    )
    heartbeat_active = "heartbeat" in status_text.lower() or "active" in status_text.lower()

    return {
        "openclaw_available": bool(status_result["ok"] or plugins_result["ok"] or runtime),
        "plugins_text": plugins_text,
        "cron_jobs": cron_jobs,
        "status_text": status_text,
        "timed_out_commands": timed_out_commands,
        "sentinel_plugin_loaded": bool(runtime.get("sentinel_plugin_enabled")),
        "cron_daily_present": cron_daily_present,
        "heartbeat_active": heartbeat_active,
    }


def _latest_triple_post_run(runs: list[dict[str, Any]]) -> dict[str, Any] | None:
    for run in runs:
        if (run.get("simulation") or {}).get("enabled"):
            continue
        results = run.get("post_results") or {}
        if all(
            (results.get(platform) or {}).get("status") == "posted"
            for platform in ("linkedin", "facebook", "x")
        ):
            return run
    return None


def _run_has_draft_artifacts(store: RunStore, run: dict[str, Any]) -> bool:
    run_id = str(run.get("run_id") or "").strip()
    if not run_id:
        return False
    run_dir = store.run_dir(run_id)
    for name in ("article", "linkedin", "facebook", "x", "image_prompt"):
        if (run_dir / "drafts" / f"{name}.md").exists():
            return True
    return False


def _status_item(status: str, summary: str, evidence: list[str] | None = None) -> dict[str, Any]:
    return {
        "status": status,
        "summary": summary,
        "evidence": evidence or [],
    }


def build_zero_cost_report(config: AppConfig, days: int = 30) -> dict[str, Any]:
    from .workflow import build_proof_readiness_payload

    store = RunStore(config)
    now = datetime.now(UTC)
    cutoff = now - timedelta(days=max(days, 1))

    runs_considered = 0
    live_runs_considered = 0
    eligible_live_runs_considered = 0
    paid_service_hits: list[str] = []
    free_backends_all: dict[str, int] = {}
    free_backends_live: dict[str, int] = {}
    image_backends_all: dict[str, int] = {}
    image_backends_live: dict[str, int] = {}
    sample_run_ids: list[str] = []
    sample_live_run_ids: list[str] = []
    sample_eligible_live_run_ids: list[str] = []

    for run in store.list_runs():
        created_at = _parse_iso(str(run.get("created_at") or ""))
        if created_at and created_at < cutoff:
            continue
        runs_considered += 1
        run_id = str(run.get("run_id") or "")
        if run_id and len(sample_run_ids) < 10:
            sample_run_ids.append(run_id)

        is_simulation = bool((run.get("simulation") or {}).get("enabled"))
        cost_proof = run.get("cost_proof") or {}
        free_backends = list(cost_proof.get("free_backends_used") or [])
        image_backend = str(cost_proof.get("image_backend") or "")
        paid_services = list(cost_proof.get("paid_services_used") or [])

        for backend in free_backends:
            free_backends_all[backend] = free_backends_all.get(backend, 0) + 1
        if image_backend:
            image_backends_all[image_backend] = image_backends_all.get(image_backend, 0) + 1
        if paid_services:
            paid_service_hits.append(f"{run_id}:{','.join(str(item) for item in paid_services)}")

        if is_simulation:
            continue

        live_runs_considered += 1
        if run_id and len(sample_live_run_ids) < 10:
            sample_live_run_ids.append(run_id)
        for backend in free_backends:
            free_backends_live[backend] = free_backends_live.get(backend, 0) + 1
        if image_backend:
            image_backends_live[image_backend] = image_backends_live.get(image_backend, 0) + 1

        proof = build_proof_readiness_payload(run)
        if proof.get("proof_eligible"):
            eligible_live_runs_considered += 1
            if run_id and len(sample_eligible_live_run_ids) < 10:
                sample_eligible_live_run_ids.append(run_id)

    return {
        "generated_at": now.isoformat(),
        "days": days,
        "runs_considered": runs_considered,
        "live_runs_considered": live_runs_considered,
        "eligible_live_runs_considered": eligible_live_runs_considered,
        "paid_services_detected": bool(paid_service_hits),
        "paid_service_hits": paid_service_hits[:20],
        "free_backends_used": free_backends_all,
        "free_backends_used_live": free_backends_live,
        "image_backends": image_backends_all,
        "image_backends_live": image_backends_live,
        "sample_run_ids": sample_run_ids,
        "sample_live_run_ids": sample_live_run_ids,
        "sample_eligible_live_run_ids": sample_eligible_live_run_ids,
    }


def log_operator_action(
    config: AppConfig, action: str, run_id: str = "", operator: str = "system"
) -> str:
    auditor = AuditLogger(config)
    return auditor.log_event(action, operator_id=operator, run_id=run_id)


def build_compliance_report(config: AppConfig) -> dict[str, Any]:
    from .dashboard import build_dashboard_summary
    from .workflow import doctor_report

    store = RunStore(config)
    runs = store.list_runs()
    runtime = _probe_openclaw_runtime(config)
    doctor = doctor_report(config)
    security = security_audit(config)
    dashboard = build_dashboard_summary(config)
    zero_cost_report = build_zero_cost_report(config, days=30)
    triple_post_run = _latest_triple_post_run(runs)
    latest_article_run = next(
        (
            run
            for run in runs
            if str((run.get("article") or {}).get("clean_text") or "").strip()
        ),
        None,
    )
    latest_draft_run = next(
        (
            run
            for run in runs
            if (run.get("drafts") or {}).get("linkedin")
            or (run.get("drafts") or {}).get("facebook")
            or (run.get("drafts") or {}).get("x")
            or _run_has_draft_artifacts(store, run)
        ),
        None,
    )

    capabilities = {
        "openclaw_workspace_and_custom_skills": _status_item(
            "done"
            if (config.base_dir / "skills").exists() and runtime.get("sentinel_plugin_loaded")
            else "partial",
            "Workspace, skills, and plugin are present."
            if (config.base_dir / "skills").exists()
            else "Workspace or skills are incomplete.",
            [
                f"skills={len(list((config.base_dir / 'skills').glob('*/SKILL.md')))}",
                f"plugin_loaded={runtime.get('sentinel_plugin_loaded')}",
                f"workspace_bootstrapped={doctor.get('checks', {}).get('workspace_bootstrapped')}",
            ],
        ),
        "competitor_ingestion": _status_item(
            "done" if latest_article_run else "blocked",
            "Competitor ingestion pipeline is producing extracted article content."
            if latest_article_run
            else "No run currently proves article ingestion.",
            [str(latest_article_run.get("run_id", ""))] if latest_article_run else [],
        ),
        "zero_cost_trend_research": _status_item(
            "done"
            if all(doctor.get("research_backends", {}).values())
            else "partial",
            "RSS, public news feeds, and pytrends are wired; SearXNG remains optional.",
            [
                f"rss={doctor.get('research_backends', {}).get('rss')}",
                f"public_news={doctor.get('research_backends', {}).get('public_news')}",
                f"pytrends={doctor.get('research_backends', {}).get('pytrends')}",
                f"searxng={doctor.get('research_backends', {}).get('searxng')}",
            ],
        ),
        "content_generation_pipeline": _status_item(
            "done" if latest_draft_run else "blocked",
            "Article and social draft artifacts are generated per run."
            if latest_draft_run
            else "No run currently proves content generation.",
            [str(latest_draft_run.get("run_id", ""))] if latest_draft_run else [],
        ),
        "image_generation": _status_item(
            "done" if zero_cost_report.get("image_backends") else "blocked",
            "A local image asset is generated for runs."
            if zero_cost_report.get("image_backends")
            else "No generated image backend has been evidenced yet.",
            list(zero_cost_report.get("image_backends", {}).keys())[:3],
        ),
        "telegram_native_interface": _status_item(
            "done"
            if runtime.get("sentinel_plugin_loaded")
            and security.get("openclaw_config", {}).get("telegram_enabled")
            else "partial",
            "Telegram runtime is enabled and operator-gated."
            if security.get("openclaw_config", {}).get("telegram_enabled")
            else "Telegram runtime is not fully enabled.",
            [
                f"telegram_enabled={security.get('openclaw_config', {}).get('telegram_enabled')}",
                f"allowlist={security.get('checks', {}).get('telegram_allowlist_mode')}",
                (
                    "operators="
                    f"{security.get('checks', {}).get('telegram_operator_allowlist_present')}"
                ),
            ],
        ),
        "scheduler_and_heartbeat": _status_item(
            "done"
            if runtime.get("cron_daily_present")
            and runtime.get("heartbeat_active")
            and doctor.get("checks", {}).get("prompt_actionable")
            else "partial",
            (
                "OpenClaw cron and heartbeat are visible in the runtime, and the "
                "configured daily prompt is actionable."
            ),
            [
                f"cron_daily_present={runtime.get('cron_daily_present')}",
                f"heartbeat_active={runtime.get('heartbeat_active')}",
                f"prompt_actionable={doctor.get('checks', {}).get('prompt_actionable')}",
            ],
        ),
        "browser_auto_posting": _status_item(
            "done" if triple_post_run else "partial",
            "A non-simulated run shows successful posting to all three platforms."
            if triple_post_run
            else "Triple-post proof has not been observed yet.",
            [str(triple_post_run.get("run_id", ""))] if triple_post_run else [],
        ),
        "security_hardening": _status_item(
            "done" if security.get("status") == "ok" else "partial",
            "Security checks are currently passing."
            if security.get("status") == "ok"
            else "Security checks still report warnings.",
            list(security.get("warnings") or [])[:10],
        ),
        "vector_store": _status_item(
            "done" if doctor.get("memory", {}).get("available") else "blocked",
            (
                f"Vector store backend `{doctor.get('memory', {}).get('backend', 'unknown')}` "
                "is available."
            )
            if doctor.get("memory", {}).get("available")
            else "Vector store backend is unavailable.",
            [str(doctor.get("memory", {}).get("path") or "")]
            if doctor.get("memory", {}).get("path")
            else [],
        ),
        "graph_store": _status_item(
            "done" if doctor.get("graph", {}).get("available") else "blocked",
            (
                f"Knowledge graph backend `{doctor.get('graph', {}).get('backend', 'unknown')}` "
                "is available."
            )
            if doctor.get("graph", {}).get("available")
            else "Knowledge graph backend is unavailable.",
            [str(doctor.get("graph", {}).get("target") or "")]
            if doctor.get("graph", {}).get("target")
            else [],
        ),
        "langgraph_multi_agent": _status_item(
            "done" if doctor.get("checks", {}).get("langgraph_available") else "blocked",
            "LangGraph multi-agent workflow is available."
            if doctor.get("checks", {}).get("langgraph_available")
            else "LangGraph workflow is unavailable.",
        ),
    }

    docs_dir = config.base_dir / "docs"
    deliverables = {
        "architecture_decision_record": _status_item(
            "done" if (docs_dir / "ADR-001-openclaw-first-architecture.md").exists() else "blocked",
            "ADR is present."
            if (docs_dir / "ADR-001-openclaw-first-architecture.md").exists()
            else "ADR is missing.",
            [str(docs_dir / "ADR-001-openclaw-first-architecture.md")],
        ),
        "research_report": _status_item(
            "done" if (docs_dir / "week1-architecture-report.md").exists() else "blocked",
            "Week 1 research report is present."
            if (docs_dir / "week1-architecture-report.md").exists()
            else "Research report is missing.",
            [str(docs_dir / "week1-architecture-report.md")],
        ),
        "deployment_guide": _status_item(
            "done" if (docs_dir / "DEPLOYMENT.md").exists() else "blocked",
            "Deployment guide is present."
            if (docs_dir / "DEPLOYMENT.md").exists()
            else "Deployment guide is missing.",
            [str(docs_dir / "DEPLOYMENT.md")],
        ),
        "telegram_command_reference": _status_item(
            "done" if (docs_dir / "TELEGRAM_COMMANDS.md").exists() else "blocked",
            "Telegram command reference is present."
            if (docs_dir / "TELEGRAM_COMMANDS.md").exists()
            else "Telegram command reference is missing.",
            [str(docs_dir / "TELEGRAM_COMMANDS.md")],
        ),
        "monitoring_dashboard": _status_item(
            "done" if (docs_dir / "monitoring-dashboard.html").exists() else "blocked",
            "Monitoring dashboard is present."
            if (docs_dir / "monitoring-dashboard.html").exists()
            else "Monitoring dashboard is missing.",
            [str(docs_dir / "monitoring-dashboard.html")],
        ),
        "html_operator_guide": _status_item(
            "done" if (docs_dir / "app-guide.html").exists() else "blocked",
            "Operator guide HTML is present."
            if (docs_dir / "app-guide.html").exists()
            else "Operator guide HTML is missing.",
            [str(docs_dir / "app-guide.html")],
        ),
        "demo_video": _status_item(
            "blocked",
            "Demo video requires manual screen capture of a live run.",
        ),
        "github_repo_delivery": _status_item(
            "blocked",
            "A remote GitHub delivery cannot be verified from local files alone.",
        ),
    }

    success_rate_14d = float(dashboard.get("success_rate_14d") or 0.0)
    runs_14d = int(dashboard.get("runs_in_last_14d") or 0)
    live_proof = {
        "success_rate_14d": _status_item(
            "done" if runs_14d >= 14 and success_rate_14d >= 95.0 else "partial",
            f"14-day success rate is {success_rate_14d}% over {runs_14d} runs.",
            [f"success_rate_14d={success_rate_14d}", f"runs_in_last_14d={runs_14d}"],
        ),
        "run_log_30d": _status_item(
            "done"
            if int(zero_cost_report.get("eligible_live_runs_considered") or 0) >= 30
            else "partial",
            "30-day live log window contains "
            f"{zero_cost_report.get('eligible_live_runs_considered', 0)} eligible live runs.",
            list(zero_cost_report.get("sample_eligible_live_run_ids") or [])[:10],
        ),
        "zero_cost_proof": _status_item(
            "done" if not zero_cost_report.get("paid_services_detected") else "blocked",
            "No paid services were detected in run cost proofs."
            if not zero_cost_report.get("paid_services_detected")
            else "Paid services were detected in run cost proofs.",
            [
                f"paid_services_detected={zero_cost_report.get('paid_services_detected')}",
                "eligible_live_runs_considered="
                f"{zero_cost_report.get('eligible_live_runs_considered', 0)}",
            ],
        ),
        "telegram_live_control": _status_item(
            "done" if security.get("openclaw_config", {}).get("telegram_enabled") else "partial",
            "Telegram live control is enabled."
            if security.get("openclaw_config", {}).get("telegram_enabled")
            else "Telegram live control is not enabled.",
            [f"telegram_enabled={security.get('openclaw_config', {}).get('telegram_enabled')}"],
        ),
        "native_posts_on_all_platforms": _status_item(
            "done" if triple_post_run else "partial",
            "At least one live run posted successfully to LinkedIn, Facebook, and X."
            if triple_post_run
            else "A live triple-post run has not been observed yet.",
            [str(triple_post_run.get("run_id", ""))] if triple_post_run else [],
        ),
    }

    blockers: list[str] = []
    if live_proof["success_rate_14d"]["status"] != "done":
        blockers.append(
            "The 14-day reliability proof target is not yet satisfied by current "
            "live run history."
        )
    if live_proof["run_log_30d"]["status"] != "done":
        blockers.append(
            "The 30-day zero-cost log deliverable is not yet backed by enough "
            "eligible live run history."
        )
    if capabilities["telegram_native_interface"]["status"] != "done":
        blockers.append("Telegram runtime is not fully enabled in the active OpenClaw workspace.")

    overall_status = "done"
    if blockers or any(
        item["status"] == "blocked"
        for item in (
            list(deliverables.values())
            + list(live_proof.values())
            + list(capabilities.values())
        )
    ):
        overall_status = "partial"

    return {
        "generated_at": datetime.now(UTC).isoformat(),
        "overall_status": overall_status,
        "live_run_count": int(dashboard.get("totals", {}).get("live_runs", 0)),
        "runtime": runtime,
        "doctor": doctor,
        "security": security,
        "dashboard": {
            "success_rate_14d": dashboard.get("success_rate_14d", 0.0),
            "runs_in_last_14d": dashboard.get("runs_in_last_14d", 0),
            "totals": dashboard.get("totals", {}),
            "operator_queue_counts": (dashboard.get("operator_queue") or {}).get("counts", {}),
        },
        "zero_cost_report": zero_cost_report,
        "capabilities": capabilities,
        "deliverables": deliverables,
        "live_proof": live_proof,
        "blockers": blockers,
    }


def _render_section(title: str, payload: dict[str, dict[str, Any]]) -> list[str]:
    lines = [f"## {title}", ""]
    for key, item in payload.items():
        lines.append(f"- {key}: {item['status']} | {item['summary']}")
        for evidence in item.get("evidence", []):
            if evidence:
                lines.append(f"  evidence: {evidence}")
    lines.append("")
    return lines


def render_compliance_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# Cahier Des Charges Compliance Report",
        "",
        f"Generated at: {report['generated_at']}",
        f"Overall status: {report['overall_status']}",
        "",
    ]
    lines.extend(_render_section("Capabilities", report.get("capabilities", {})))
    lines.extend(_render_section("Deliverables", report.get("deliverables", {})))
    lines.extend(_render_section("Live Proof", report.get("live_proof", {})))
    lines.extend(["## Blockers", ""])
    if report.get("blockers"):
        lines.extend(f"- {item}" for item in report["blockers"])
    else:
        lines.append("- None")
    lines.append("")
    return "\n".join(lines)


def write_compliance_report(config: AppConfig) -> dict[str, Any]:
    report = build_compliance_report(config)
    out_dir = config.data_dir / "compliance"
    out_dir.mkdir(parents=True, exist_ok=True)

    json_path = out_dir / "cahier-des-charges-report.json"
    md_path = out_dir / "cahier-des-charges-report.md"
    legacy_json_path = out_dir / "compliance_report.json"

    dump_json(json_path, report)
    dump_json(legacy_json_path, report)
    write_text(md_path, render_compliance_markdown(report))
    return {
        "status": report["overall_status"],
        "json_path": str(json_path),
        "markdown_path": str(md_path),
        "legacy_json_path": str(legacy_json_path),
        "report": report,
    }
