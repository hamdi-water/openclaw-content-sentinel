from __future__ import annotations

import hashlib
from collections import Counter
from datetime import UTC, datetime
from typing import Any

from .config import AppConfig
from .storage import RunStore
from .utils import dump_json, ensure_dir, write_text


def _sha256_text(value: str) -> str:
    return hashlib.sha256((value or "").encode("utf-8")).hexdigest()


def build_promptops_report(config: AppConfig) -> dict[str, Any]:
    store = RunStore(config)
    runs = store.list_runs()
    prompt_hashes: Counter[str] = Counter()
    image_prompt_hashes: Counter[str] = Counter()
    issue_counter: Counter[str] = Counter()
    publish_blocker_counter: Counter[str] = Counter()
    approval_outcomes: Counter[str] = Counter()
    golden_set = []

    for run in runs:
        prompt = str(run.get("prompt") or "")
        if prompt:
            prompt_hashes[_sha256_text(prompt)] += 1
        quality_gate = run.get("quality_gate") or {}
        for issue in quality_gate.get("issues") or []:
            issue_counter[str(issue)] += 1
        for blocker in quality_gate.get("publish_blockers") or []:
            publish_blocker_counter[str(blocker)] += 1
        approval_outcomes[str(run.get("approval_state") or "pending")] += 1
        if not (run.get("simulation") or {}).get("enabled") and run.get("status") == "posted":
            golden_set.append(
                {
                    "run_id": run.get("run_id", ""),
                    "prompt_hash": _sha256_text(prompt),
                    "schema_version": run.get("schema_version", ""),
                    "publish_adapter_version": run.get("publish_adapter_version", ""),
                }
            )
        image_prompt_path = store.run_dir(run.get("run_id", "")) / "drafts" / "image_prompt.md"
        if image_prompt_path.exists():
            image_prompt_hashes[_sha256_text(image_prompt_path.read_text(encoding="utf-8"))] += 1

    default_prompt_text = (
        config.default_prompt_file.read_text(encoding="utf-8")
        if config.default_prompt_file.exists()
        else ""
    )
    report = {
        "generated_at": datetime.now(UTC).isoformat(),
        "prompt_bundle": {
            "default_prompt_file": str(config.default_prompt_file),
            "default_prompt_hash": _sha256_text(default_prompt_text) if default_prompt_text else "",
            "schema_version": config.schema_version,
        },
        "registry": {
            "unique_run_prompt_hashes": len(prompt_hashes),
            "unique_image_prompt_hashes": len(image_prompt_hashes),
            "latest_live_prompt_hashes": list(prompt_hashes.keys())[:10],
            "latest_image_prompt_hashes": list(image_prompt_hashes.keys())[:10],
        },
        "quality_taxonomy": {
            "issues": dict(issue_counter.most_common()),
            "publish_blockers": dict(publish_blocker_counter.most_common()),
            "approval_outcomes": dict(approval_outcomes),
        },
        "golden_set": {
            "count": len(golden_set),
            "runs": golden_set[:20],
        },
    }
    return report


def render_promptops_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# PromptOps Report",
        "",
        f"Generated at: {report['generated_at']}",
        f"Default prompt file: {report['prompt_bundle']['default_prompt_file']}",
        f"Default prompt hash: {report['prompt_bundle']['default_prompt_hash']}",
        f"Schema version: {report['prompt_bundle']['schema_version']}",
        "",
        "## Registry",
        "",
        f"- Unique run prompt hashes: {report['registry']['unique_run_prompt_hashes']}",
        f"- Unique image prompt hashes: {report['registry']['unique_image_prompt_hashes']}",
        "",
        "## Quality Taxonomy",
        "",
    ]
    for key, values in report["quality_taxonomy"].items():
        lines.append(f"### {key}")
        if values:
            for name, count in values.items():
                lines.append(f"- {name}: {count}")
        else:
            lines.append("- None")
        lines.append("")
    lines.append("## Golden Set")
    lines.append("")
    lines.append(f"- Count: {report['golden_set']['count']}")
    for item in report["golden_set"]["runs"]:
        lines.append(f"- {item['run_id']} | prompt_hash={item['prompt_hash']}")
    lines.append("")
    return "\n".join(lines)


def write_promptops_report(config: AppConfig) -> dict[str, Any]:
    report = build_promptops_report(config)
    out_dir = ensure_dir(config.data_dir / "promptops")
    json_path = out_dir / "promptops-report.json"
    md_path = out_dir / "promptops-report.md"
    dump_json(json_path, report)
    write_text(md_path, render_promptops_markdown(report))
    return {
        "json_path": str(json_path),
        "markdown_path": str(md_path),
        "report": report,
    }
