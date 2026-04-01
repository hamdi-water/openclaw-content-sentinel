from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from .compliance import build_compliance_report
from .config import AppConfig
from .operations import build_scheduler_health_report
from .security import security_audit
from .utils import dump_json, ensure_dir, write_text


def build_release_readiness_report(
    config: AppConfig, gap_report: dict[str, Any] | None = None
) -> dict:
    compliance = build_compliance_report(config)
    gap = gap_report or {
        "overall_completion_percent": 0.0,
        "top_gaps": [],
    }
    scheduler = build_scheduler_health_report(config)
    security = security_audit(config)
    blockers = list(compliance.get("blockers", []))
    if scheduler.get("action_items"):
        blockers.extend(scheduler["action_items"])
    readiness = "go" if gap["overall_completion_percent"] >= 90 and not blockers else "hold"
    return {
        "generated_at": datetime.now(UTC).isoformat(),
        "readiness": readiness,
        "gap_completion_percent": gap.get("overall_completion_percent", 0.0),
        "scheduler_status": scheduler["status"],
        "security_status": security["status"],
        "blockers": blockers,
        "top_gaps": gap.get("top_gaps", []),
    }


def render_release_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# Release Readiness",
        "",
        f"Generated at: {report['generated_at']}",
        f"Readiness: {report['readiness']}",
        f"Gap completion: {report['gap_completion_percent']}",
        f"Scheduler status: {report['scheduler_status']}",
        f"Security status: {report['security_status']}",
        "",
        "## Blockers",
        "",
    ]
    if report["blockers"]:
        for item in report["blockers"]:
            lines.append(f"- {item}")
    else:
        lines.append("- None")
    lines.extend(["", "## Top Gaps", ""])
    for item in report.get("top_gaps", []):
        lines.append(f"- {item['title']}: {item['summary']}")
    lines.append("")
    return "\n".join(lines)


def write_release_readiness_report(
    config: AppConfig, gap_report: dict[str, Any] | None = None
) -> dict:
    report = build_release_readiness_report(config, gap_report=gap_report)
    out_dir = ensure_dir(config.data_dir / "release")
    json_path = out_dir / "release-readiness.json"
    md_path = out_dir / "release-readiness.md"
    dump_json(json_path, report)
    write_text(md_path, render_release_markdown(report))
    return {
        "json_path": str(json_path),
        "markdown_path": str(md_path),
        "report": report,
    }
