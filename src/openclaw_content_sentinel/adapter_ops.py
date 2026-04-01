from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from .adapter_state import clear_adapter_freeze, get_adapter_runtime
from .browser_automation import resolve_platform_spec
from .config import AppConfig
from .selector_registry import load_selector_registry, selector_override_for
from .utils import dump_json, ensure_dir, load_json, write_text


def build_adapter_manifest(config: AppConfig) -> dict[str, Any]:
    items: list[dict[str, Any]] = []
    browser_health_dir = config.data_dir / "browser-health"
    registry = load_selector_registry(config)
    for platform in ("linkedin", "facebook", "x"):
        spec = resolve_platform_spec(config, platform)
        health_path = browser_health_dir / f"{platform}.json"
        health = load_json(health_path, default={}) or {}
        adapter_runtime = get_adapter_runtime(config, platform)
        selector_override = selector_override_for(config, platform)
        items.append(
            {
                "platform": platform,
                "compose_url": spec.compose_url,
                "success_texts": list(spec.success_texts),
                "composer_pattern_count": len(spec.composer_patterns),
                "submit_pattern_count": len(spec.submit_patterns),
                "media_pattern_count": len(spec.media_patterns),
                "file_input_selector": spec.file_input_selector,
                "health_status": health.get("status", "unknown"),
                "health_reason": health.get("reason", ""),
                "selector_override_applied": bool(selector_override),
                "registry_version": registry.get("registry_version", ""),
                "recent_failure_count": adapter_runtime.get("recent_failure_count", 0),
                "freeze": adapter_runtime.get("freeze") or {},
                "last_fixture_pack": adapter_runtime.get("last_fixture_pack", ""),
            }
        )
    return {
        "generated_at": datetime.now(UTC).isoformat(),
        "publish_adapter_version": config.publish_adapter_version,
        "selector_registry": {
            "path": registry.get("path", ""),
            "exists": registry.get("exists", False),
            "registry_version": registry.get("registry_version", ""),
        },
        "controls": {
            "adapter_failure_threshold": config.adapter_failure_threshold,
            "adapter_failure_window_minutes": config.adapter_failure_window_minutes,
            "adapter_freeze_minutes": config.adapter_freeze_minutes,
        },
        "items": items,
    }


def render_adapter_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# Adapter Engineering Manifest",
        "",
        f"Generated at: {report['generated_at']}",
        f"Publish adapter version: {report['publish_adapter_version']}",
        f"Selector registry: {report['selector_registry']['registry_version']}",
        f"Freeze threshold: {report['controls']['adapter_failure_threshold']}",
        "",
    ]
    for item in report["items"]:
        lines.extend(
            [
                f"## {item['platform']}",
                "",
                f"- Compose URL: {item['compose_url']}",
                f"- Health: {item['health_status']} ({item['health_reason'] or 'ok'})",
                f"- Composer patterns: {item['composer_pattern_count']}",
                f"- Submit patterns: {item['submit_pattern_count']}",
                f"- Media patterns: {item['media_pattern_count']}",
                f"- File input selector: {item['file_input_selector']}",
                f"- Selector override applied: {item['selector_override_applied']}",
                f"- Recent failures: {item['recent_failure_count']}",
                f"- Freeze active: {bool((item.get('freeze') or {}).get('active'))}",
                f"- Last fixture pack: {item.get('last_fixture_pack', '') or 'n/a'}",
                "",
            ]
        )
    return "\n".join(lines)


def write_adapter_manifest(config: AppConfig) -> dict[str, Any]:
    report = build_adapter_manifest(config)
    out_dir = ensure_dir(config.data_dir / "adapters")
    json_path = out_dir / "adapter-manifest.json"
    md_path = out_dir / "adapter-manifest.md"
    dump_json(json_path, report)
    write_text(md_path, render_adapter_markdown(report))
    return {
        "json_path": str(json_path),
        "markdown_path": str(md_path),
        "report": report,
    }


def unfreeze_adapter(config: AppConfig, platform: str, actor: str = "operator") -> dict[str, Any]:
    runtime = clear_adapter_freeze(config, platform, actor=actor)
    return {"platform": platform, "adapter_runtime": runtime}
