# ruff: noqa: E501
from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from .config import AppConfig, _load_dotenv
from .security import load_openclaw_runtime_config
from .storage import RunStore
from .utils import dump_json, ensure_dir, now_utc

TELEGRAM_COMMANDS = [
    {"command": "ocs_security", "description": "Audit local security"},
    {"command": "ocs_compliance", "description": "Show compliance gaps"},
    {"command": "ocs_runtime", "description": "Inspect runtime config"},
    {"command": "ocs_scheduler", "description": "Audit scheduler health"},
    {"command": "ocs_queue", "description": "Show operator action queue"},
    {"command": "ocs_briefs", "description": "Show the daily brief queue"},
    {"command": "ocs_promote_brief", "description": "Promote one queued brief"},
    {"command": "ocs_browser_health", "description": "Check browser profiles"},
    {"command": "ocs_doctor", "description": "Check workspace readiness"},
    {"command": "ocs_dashboard", "description": "Show dashboard summary"},
    {"command": "ocs_status", "description": "Show recent runs"},
    {"command": "ocs_run_now", "description": "Trigger a new run"},
    {"command": "ocs_approve", "description": "Approve a run"},
    {"command": "ocs_reject", "description": "Reject a run"},
    {"command": "ocs_logs", "description": "Show run logs"},
    {"command": "ocs_pause", "description": "Pause automation"},
    {"command": "ocs_resume", "description": "Resume automation"},
    {"command": "ocs_stage_publish", "description": "Prepare publish metadata"},
    {"command": "ocs_publish", "description": "Run browser publish"},
    {"command": "ocs_retry", "description": "Retry a failed publish"},
]


def _parse_iso(value: str) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _operator_resolved(run: dict[str, Any]) -> bool:
    resolution = run.get("operator_resolution") or {}
    return bool(resolution.get("resolved"))


def _set_operator_resolution(run: dict[str, Any], code: str, note: str) -> dict[str, Any]:
    resolution = dict(run.get("operator_resolution") or {})
    resolution.update(
        {
            "resolved": True,
            "resolution_code": code,
            "resolution_note": note,
            "resolved_at": now_utc(),
        }
    )
    run["operator_resolution"] = resolution
    notes = list(run.get("notes") or [])
    if note and note not in notes:
        notes.append(note)
    run["notes"] = notes
    return run


def _run_openclaw(config: AppConfig, *args: str) -> dict[str, Any]:
    executable = shutil.which(config.openclaw_bin) or config.openclaw_bin
    # noqa: S603
    result = subprocess.run(  # noqa: S603
        [executable, *args],
        cwd=config.base_dir,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=30,
        check=False,
    )
    return {
        "ok": result.returncode == 0,
        "stdout": (result.stdout or "").strip(),
        "stderr": (result.stderr or "").strip(),
        "returncode": result.returncode,
    }


def runtime_status(config: AppConfig) -> dict[str, Any]:
    runtime = load_openclaw_runtime_config()
    telegram = (runtime.get("channels") or {}).get("telegram") or {}
    plugin = ((runtime.get("plugins") or {}).get("entries") or {}).get("sentinel-ops") or {}
    model = (((runtime.get("agents") or {}).get("defaults") or {}).get("model") or {}).get(
        "primary", ""
    )
    memory_cfg = ((runtime.get("agents") or {}).get("defaults") or {}).get("memorySearch") or {}
    return {
        "config_path": str(Path.home() / ".openclaw" / "openclaw.json"),
        "telegram_enabled": bool(telegram.get("enabled")),
        "telegram_allowlist_count": len(telegram.get("allowFrom") or []),
        "telegram_custom_commands": [
            item.get("command", "") if isinstance(item, dict) else str(item)
            for item in (telegram.get("customCommands") or [])
        ],
        "sentinel_plugin_enabled": bool(plugin.get("enabled")),
        "sentinel_workspace_dir": ((plugin.get("config") or {}).get("workspaceDir") or ""),
        "primary_model": model,
        "memory_search_enabled": bool(memory_cfg.get("enabled")),
    }


def sync_runtime_config(config: AppConfig, restart_gateway: bool = False) -> dict[str, Any]:
    dotenv = _load_dotenv(config.base_dir)
    bot_token = dotenv.get("OPENCLAW_TELEGRAM_BOT_TOKEN", "")
    allowed_users_raw = dotenv.get("OPENCLAW_TELEGRAM_ALLOWED_USERS", "")
    allowed_users = [item.strip() for item in allowed_users_raw.split(",") if item.strip()]
    ops_applied: list[str] = []
    warnings: list[str] = []

    batch_ops = [
        {"path": "plugins.entries.sentinel-ops.enabled", "value": True},
        {"path": "plugins.entries.sentinel-ops.config.workspaceDir", "value": str(config.base_dir)},
        {"path": "plugins.entries.sentinel-ops.config.pythonPath", "value": "python"},
        {
            "path": "plugins.entries.sentinel-ops.config.cliModule",
            "value": "openclaw_content_sentinel.cli",
        },
        {
            "path": "plugins.entries.sentinel-ops.config.promptFile",
            "value": str(config.default_prompt_file),
        },
        {
            "path": "plugins.entries.sentinel-ops.config.defaultTargets",
            "value": list(config.default_targets),
        },
    ]
    if config.preferred_model:
        batch_ops.append({"path": "agents.defaults.model.primary", "value": config.preferred_model})
    if bot_token and allowed_users:
        batch_ops.extend(
            [
                {"path": "channels.telegram.enabled", "value": True},
                {"path": "channels.telegram.dmPolicy", "value": "allowlist"},
                {"path": "channels.telegram.allowFrom", "value": allowed_users},
                {"path": "channels.telegram.commands.native", "value": True},
                {"path": "channels.telegram.customCommands", "value": TELEGRAM_COMMANDS},
            ]
        )
    else:
        warnings.append(
            "Telegram token or allowed users are missing in .env; Telegram runtime was not enabled."
        )

    with tempfile.NamedTemporaryFile(
        suffix=".json", mode="w", encoding="utf-8", delete=False
    ) as handle:
        json.dump(batch_ops, handle, ensure_ascii=True)
        batch_path = Path(handle.name)

    try:
        if bot_token and allowed_users:
            token_result = _run_openclaw(
                config,
                "config",
                "set",
                "channels.telegram.botToken",
                "--ref-provider",
                "default",
                "--ref-source",
                "env",
                "--ref-id",
                "OPENCLAW_TELEGRAM_BOT_TOKEN",
            )
            if token_result["ok"]:
                ops_applied.append("channels.telegram.botToken -> env ref")
            else:
                warnings.append(
                    token_result["stderr"]
                    or token_result["stdout"]
                    or "Unable to set Telegram token ref."
                )

        batch_result = _run_openclaw(config, "config", "set", "--batch-file", str(batch_path))
        if batch_result["ok"]:
            ops_applied.append("batch runtime config")
        else:
            warnings.append(
                batch_result["stderr"]
                or batch_result["stdout"]
                or "Unable to apply runtime batch config."
            )

        validate_result = _run_openclaw(config, "config", "validate")
        if validate_result["ok"]:
            ops_applied.append("config validate")
        else:
            warnings.append(
                validate_result["stderr"]
                or validate_result["stdout"]
                or "Runtime config validation failed."
            )

        if restart_gateway:
            restart_result = _run_openclaw(config, "gateway", "restart")
            if restart_result["ok"]:
                ops_applied.append("gateway restart")
            else:
                warnings.append(
                    restart_result["stderr"]
                    or restart_result["stdout"]
                    or "Gateway restart failed."
                )
    finally:
        batch_path.unlink(missing_ok=True)

    return {
        "status": "ok" if ops_applied and not warnings else "attention",
        "operations_applied": ops_applied,
        "warnings": warnings,
        "runtime": runtime_status(config),
    }


def build_scheduler_health_report(config: AppConfig) -> dict[str, Any]:
    store = RunStore(config)
    runs = store.list_runs()
    runtime = runtime_status(config)
    dotenv = _load_dotenv(config.base_dir)
    now = datetime.now(UTC)
    today = now.date()
    stale_cutoff = now - timedelta(hours=12)

    stale_approvals = []
    preview_missing = []
    posting_failed = []
    for run in runs:
        if _operator_resolved(run):
            continue
        created_at = _parse_iso(run.get("created_at", ""))
        if run.get("status") == "awaiting_approval" and created_at and created_at < stale_cutoff:
            stale_approvals.append(run.get("run_id", ""))
        if run.get("status") in {"awaiting_approval", "approved"} and not run.get(
            "telegram_preview_sent_at"
        ):
            preview_missing.append(run.get("run_id", ""))
        if run.get("status") == "posting_failed":
            posting_failed.append(run.get("run_id", ""))

    today_runs = [
        run.get("run_id", "")
        for run in runs
        if (_parse_iso(run.get("created_at", "")) or now).date() == today
    ]
    action_items = []
    if not runtime["telegram_enabled"]:
        action_items.append("Enable Telegram runtime before unattended approval workflows.")
    if stale_approvals:
        action_items.append("Resolve stale approvals or reject stalled runs.")
    if posting_failed:
        action_items.append("Review failed posting runs and refresh browser sessions.")
    if not today_runs:
        action_items.append("No run exists for today; verify cron input and scheduler health.")
    prompt_text = dotenv.get("OPENCLAW_SENTINEL_LAST_PROMPT", "")
    if not prompt_text and config.daily_input_file.exists():
        try:
            payload = json.loads(config.daily_input_file.read_text(encoding="utf-8"))
            prompt_text = str(payload.get("prompt") or "")
        except Exception:
            prompt_text = ""
    from .research import prompt_is_actionable

    if prompt_text and not prompt_is_actionable(prompt_text):
        action_items.append(
            "Daily prompt is still unresolved; update config/daily_input.json or promote a ready item in config/daily_brief_queue.json before unattended runs."
        )

    operator_queue = build_operator_action_queue(config)
    report = {
        "generated_at": now.isoformat(),
        "status": "ok" if not action_items else "attention",
        "runtime": runtime,
        "today_runs": today_runs,
        "stale_approvals": stale_approvals[:10],
        "preview_missing": preview_missing[:10],
        "posting_failed": posting_failed[:10],
        "action_items": action_items,
        "operator_queue_counts": operator_queue["counts"],
    }
    output_path = config.data_dir / "ops" / "scheduler-health.json"
    ensure_dir(output_path.parent)
    dump_json(output_path, report)
    report["path"] = str(output_path)
    return report


def build_operator_action_queue(config: AppConfig) -> dict[str, Any]:
    store = RunStore(config)
    runs = store.list_runs()
    runtime = runtime_status(config)
    now = datetime.now(UTC)
    stale_cutoff = now - timedelta(hours=12)
    queue = []

    def push(
        severity: str, category: str, summary: str, run_id: str = "", action: str = ""
    ) -> None:
        queue.append(
            {
                "severity": severity,
                "category": category,
                "summary": summary,
                "run_id": run_id,
                "recommended_action": action,
            }
        )

    _check_config_and_runtime(config, push, runtime)
    _check_run_health_detailed(config, push, runs, stale_cutoff)
    _check_browser_health_v2(config, push)

    severity_rank = {"high": 0, "medium": 1, "low": 2}
    queue.sort(
        key=lambda item: (severity_rank.get(item["severity"], 3), item["category"], item["run_id"])
    )
    summary = {
        "generated_at": now.isoformat(),
        "status": "ok" if not queue else "attention",
        "counts": {
            "total": len(queue),
            "high": sum(1 for item in queue if item["severity"] == "high"),
            "medium": sum(1 for item in queue if item["severity"] == "medium"),
            "low": sum(1 for item in queue if item["severity"] == "low"),
        },
        "items": queue,
    }
    output_path = config.data_dir / "ops" / "operator-action-queue.json"
    ensure_dir(output_path.parent)
    dump_json(output_path, summary)
    summary["path"] = str(output_path)
    return summary


def _check_config_and_runtime(config: AppConfig, push: Callable, runtime: dict[str, Any]) -> None:
    from .workflow import doctor_report

    doctor = doctor_report(config)
    brief_queue = doctor.get("daily_brief_queue") or {}
    queue_ready = int(brief_queue.get("ready_count") or 0)
    overdue_ready = [item for item in (brief_queue.get("briefs") or []) if item.get("overdue")]
    if not doctor["checks"].get("prompt_actionable", False):
        push(
            "high",
            "configuration",
            "The configured daily prompt is still a placeholder or incomplete.",
            action=(
                "Promote one ready brief from config/daily_brief_queue.json "
                "into config/daily_input.json."
                if queue_ready
                else "Update config/daily_input.json or add a ready brief with a topic."
            ),
        )
    if overdue_ready:
        labels = ", ".join(str(item.get("brief_id") or "") for item in overdue_ready[:3])
        push(
            "high" if not doctor["checks"].get("prompt_actionable", False) else "medium",
            "configuration",
            "At least one queued daily brief is overdue.",
            action=(f"Review overdue briefs in config/daily_brief_queue.json: {labels}."),
        )

    if not runtime["telegram_enabled"]:
        push(
            "high",
            "runtime",
            "Telegram runtime is not enabled.",
            action=("Run runtime-sync and verify the bot token plus operator allowlist."),
        )


def _check_run_health_detailed(
    config: AppConfig, push: Callable, runs: list[Any], stale_cutoff: datetime
) -> None:
    from .workflow import build_proof_readiness_payload

    for run in runs:
        run_id = run.get("run_id", "")
        if _operator_resolved(run):
            continue
        created_at = _parse_iso(run.get("created_at", ""))
        build_proof_readiness_payload(run)
        if run.get("status") == "awaiting_approval" and created_at and created_at < stale_cutoff:
            push(
                "medium",
                "approval",
                f"Run {run_id} is awaiting approval for >12h.",
                run_id=run_id,
                action="Approve or reject the run.",
            )
        if run.get("status") in {"awaiting_approval", "approved"} and not run.get(
            "telegram_preview_sent_at"
        ):
            push(
                "medium",
                "telegram",
                "Telegram preview has not been sent.",
                run_id=run_id,
                action=f"Run `ocs send-telegram-preview --run-id {run_id}`.",
            )
        if run.get("status") == "posting_failed":
            failed_platforms = []
            for platform, result in (run.get("post_results") or {}).items():
                if result.get("status") == "failed":
                    reason = result.get("failure_reason") or "unknown"
                    failed_platforms.append(f"{platform}:{reason}")
            push(
                "high",
                "publishing",
                "Live publishing failed on one or more platforms.",
                run_id=run_id,
                action=f"Inspect failures for {run_id}: {', '.join(failed_platforms)}",
            )


def _check_browser_health_v2(config: AppConfig, push: Callable) -> None:
    browser_health_dir = config.data_dir / "browser-health"
    for platform in ("linkedin", "facebook", "x"):
        status_path = browser_health_dir / f"{platform}.json"
        try:
            payload = json.loads(status_path.read_text(encoding="utf-8"))
        except Exception:  # noqa: S112
            # Skip unreadable or malformed status files
            continue
        if payload.get("status") != "ready":
            reason = payload.get("reason") or payload.get("status")
            push(
                "high"
                if payload.get("reason") in {"not_logged_in", "challenge_page"}
                else "medium",
                "browser_profile",
                f"{platform} browser profile is not ready: {reason}.",
                action=f"Refresh the {platform} persistent profile and rerun browser-health.",
            )


def sweep_operator_backlog(
    config: AppConfig,
    *,
    stale_hours: int = 12,
    resolve_failed_hours: int = 12,
    send_previews: bool = True,
) -> dict[str, Any]:
    store = RunStore(config)
    runs = store.list_runs()
    now = datetime.now(UTC)
    stale_cutoff = now - timedelta(hours=max(1, stale_hours))
    failure_cutoff = now - timedelta(hours=max(1, resolve_failed_hours))

    from .workflow import build_proof_readiness_payload, maybe_send_telegram_preview, reject_run

    resolved_failures: list[str] = []
    rejected_stale_runs: list[str] = []
    preview_sent: list[str] = []
    skipped: list[str] = []
    latest_verified_run = next(
        (
            run
            for run in runs
            if bool(
                ((run.get("analysis") or {}).get("proof_readiness") or {}).get(
                    "live_proof_complete"
                )
            )
            or run.get("status") == "posted"
        ),
        {},
    )
    latest_verified_run_id = str(latest_verified_run.get("run_id") or "")

    for run in runs:
        run_id = str(run.get("run_id") or "")
        if not run_id or _operator_resolved(run):
            continue
        created_at = _parse_iso(run.get("created_at", ""))
        proof_readiness = build_proof_readiness_payload(run)
        status = str(run.get("status") or "").strip().lower()

        if status == "awaiting_approval" and created_at and created_at < stale_cutoff:
            note = "Auto-rejected by operator maintenance sweep because the approval TTL expired."
            reject_run(config, run_id, note=note)
            rejected_stale_runs.append(run_id)
            continue

        if (
            send_previews
            and status in {"awaiting_approval", "approved"}
            and not run.get("telegram_preview_sent_at")
        ):
            preview_result = maybe_send_telegram_preview(config, run_id)
            if preview_result.get("status") == "sent":
                preview_sent.append(run_id)
            elif preview_result.get("reason") not in {"preview_already_sent", ""}:
                skipped.append(f"{run_id}:{preview_result.get('reason')}")

        if status == "posting_failed" and created_at and created_at < failure_cutoff:
            note = (
                "Historical publish failure removed from the active operator backlog. "
                "The failed run is retained for analytics and proof history."
            )
            if latest_verified_run_id:
                note = (
                    f"{note} A verified live publish exists in the workspace "
                    f"({latest_verified_run_id})."
                )
            run = store.load_run(run_id)
            _set_operator_resolution(run, "historical_publish_failure_documented", note)
            run["updated_at"] = now_utc()
            store.save_run(run)
            resolved_failures.append(run_id)

        if status == "awaiting_approval" and not proof_readiness.get("proof_eligible", False):
            skipped.append(f"{run_id}:proof_ineligible")

    scheduler = build_scheduler_health_report(config)
    queue = build_operator_action_queue(config)
    return {
        "status": "ok",
        "rejected_stale_runs": rejected_stale_runs,
        "resolved_failures": resolved_failures,
        "preview_sent": preview_sent,
        "skipped": skipped,
        "scheduler_status": scheduler.get("status"),
        "operator_queue_status": queue.get("status"),
        "remaining_action_items": scheduler.get("action_items") or [],
        "latest_verified_run_id": latest_verified_run_id,
    }
