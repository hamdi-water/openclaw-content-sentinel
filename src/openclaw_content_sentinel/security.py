from __future__ import annotations

import re
from pathlib import Path
from typing import (
    Any,
)

from .config import AppConfig
from .storage import RunStore
from .utils import load_json

LITERAL_SECRET_RE = re.compile(
    r"(token|api[_-]?key|secret|password)\s*[:=]\s*['\"][A-Za-z0-9_\-]{12,}['\"]",
    re.IGNORECASE,
)
ENV_SECRET_RE = re.compile(
    r"^(?:export\s+)?[A-Z0-9_]*(?:TOKEN|API_KEY|SECRET|PASSWORD)[A-Z0-9_]*\s*=\s*[^\s#]{12,}",
    re.IGNORECASE | re.MULTILINE,
)


def _openclaw_config_path() -> Path:
    return Path.home() / ".openclaw" / "openclaw.json"


def _load_openclaw_config() -> dict[str, Any]:
    return load_json(_openclaw_config_path(), default={}) or {}


def load_openclaw_runtime_config() -> dict[str, Any]:
    return _load_openclaw_config()


def _gitignore_contains(pattern: str, root: Path) -> bool:
    path = root / ".gitignore"
    if not path.exists():
        return False
    lines = [line.strip() for line in path.read_text(encoding="utf-8").splitlines()]
    return pattern in lines


def _scan_for_secret_leaks(root: Path) -> list[str]:
    findings = []
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        if any(part in {"workspace-data", ".venv", "__pycache__", ".git"} for part in path.parts):
            continue
        suffix = path.suffix.lower()
        if suffix not in {".py", ".md", ".json", ".jsonc", ".ps1", ".sh", ".env", ".txt"}:
            continue
        if path.name in {".env", ".env.example"}:
            continue
        try:
            content = path.read_text(encoding="utf-8", errors="ignore")
        except Exception:  # noqa: S112
            # Skip unreadable or system files during secret scanning
            continue
        if suffix == ".py":
            match = LITERAL_SECRET_RE.search(content)
        else:
            match = LITERAL_SECRET_RE.search(content) or ENV_SECRET_RE.search(content)
        if match:
            findings.append(str(path))
    return findings[:20]


def security_audit(config: AppConfig) -> dict[str, Any]:
    store = RunStore(config)
    openclaw_config = _load_openclaw_config()
    telegram_cfg = (openclaw_config.get("channels") or {}).get("telegram") or {}
    plugins = openclaw_config.get("plugins") or {}
    allow_plugins = set(plugins.get("allow") or [])
    plugin_entries = plugins.get("entries") or {}
    sentinel_entry = plugin_entries.get("sentinel-ops") or {}
    leaking_files = _scan_for_secret_leaks(config.base_dir)

    checks = {
        "workspace_data_gitignored": _gitignore_contains("workspace-data/", config.base_dir),
        "env_gitignored": _gitignore_contains(".env", config.base_dir),
        "sentinel_plugin_allowed": "sentinel-ops" in allow_plugins,
        "sentinel_plugin_enabled": bool(sentinel_entry.get("enabled")),
        "approval_required": bool(config.approval_required),
        "telegram_enabled": bool(telegram_cfg.get("enabled")),
        "telegram_allowlist_mode": str(telegram_cfg.get("dmPolicy") or "") == "allowlist",
        "telegram_operator_allowlist_present": bool(telegram_cfg.get("allowFrom")),
        "browser_profiles_named": all(
            [
                config.browser_profile_linkedin,
                config.browser_profile_facebook,
                config.browser_profile_x,
            ]
        ),
        "automation_not_paused": not store.load_control().get("automation_paused", False),
        "secret_leak_scan_clean": not leaking_files,
    }

    warnings = []
    if not checks["telegram_enabled"]:
        warnings.append(
            "Telegram native integration is not enabled in the active OpenClaw runtime."
        )
    if checks["telegram_enabled"] and not checks["telegram_allowlist_mode"]:
        warnings.append("Telegram is enabled without dmPolicy=allowlist.")
    if checks["telegram_enabled"] and not checks["telegram_operator_allowlist_present"]:
        warnings.append("Telegram is enabled without an operator allowlist.")
    if not checks["workspace_data_gitignored"] or not checks["env_gitignored"]:
        warnings.append("Critical local files are not fully protected by .gitignore.")
    if leaking_files:
        warnings.append("Potential secret-like values were detected in workspace files.")
    return {
        "status": "ok" if not warnings else "attention",
        "checks": checks,
        "warnings": warnings,
        "potential_secret_files": leaking_files,
        "openclaw_config": {
            "path": str(_openclaw_config_path()),
            "telegram_enabled": bool(telegram_cfg.get("enabled")),
            "telegram_dm_policy": telegram_cfg.get("dmPolicy", ""),
            "telegram_allow_from_count": len(telegram_cfg.get("allowFrom") or []),
        },
    }
