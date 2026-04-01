from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from .config import AppConfig
from .utils import dump_json, ensure_dir, load_json, now_utc


def _parse_iso(value: str) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _state_path(config: AppConfig) -> Path:
    return ensure_dir(config.data_dir / "adapters") / "adapter-state.json"


def _default_platform_state() -> dict[str, Any]:
    return {
        "recent_failures": [],
        "recent_failure_count": 0,
        "last_failure_at": "",
        "last_failure_reason": "",
        "last_fixture_pack": "",
        "last_run_id": "",
        "last_success_at": "",
        "last_success_url": "",
        "freeze": {
            "active": False,
            "reason": "",
            "since": "",
            "until": "",
            "trigger_failure_reason": "",
            "threshold": 0,
            "count": 0,
        },
    }


def load_adapter_state(config: AppConfig) -> dict[str, Any]:
    payload = load_json(_state_path(config), default={}) or {}
    if not isinstance(payload, dict):
        payload = {}
    platforms = payload.get("platforms")
    if not isinstance(platforms, dict):
        platforms = {}
    normalized_platforms: dict[str, dict[str, Any]] = {}
    for platform in ("linkedin", "facebook", "x"):
        raw = platforms.get(platform)
        state = dict(raw) if isinstance(raw, dict) else _default_platform_state()
        freeze = state.get("freeze")
        state["freeze"] = (
            dict(freeze)
            if isinstance(freeze, dict)
            else _default_platform_state()["freeze"]
        )
        failures = state.get("recent_failures")
        state["recent_failures"] = (
            [dict(item) for item in failures] if isinstance(failures, list) else []
        )
        state["recent_failure_count"] = len(state["recent_failures"])
        normalized_platforms[platform] = state
    return {
        "updated_at": str(payload.get("updated_at") or ""),
        "platforms": normalized_platforms,
    }


def save_adapter_state(config: AppConfig, payload: dict[str, Any]) -> dict[str, Any]:
    payload["updated_at"] = now_utc()
    dump_json(_state_path(config), payload)
    return payload


def _trim_failures(config: AppConfig, failures: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if config.adapter_failure_window_minutes <= 0:
        return failures
    cutoff = datetime.now(UTC) - timedelta(minutes=config.adapter_failure_window_minutes)
    kept: list[dict[str, Any]] = []
    for item in failures:
        timestamp = _parse_iso(str(item.get("timestamp") or ""))
        if timestamp and timestamp >= cutoff:
            kept.append(item)
    return kept


def get_adapter_runtime(config: AppConfig, platform: str) -> dict[str, Any]:
    state = load_adapter_state(config)
    platform_state = dict(
        (state.get("platforms") or {}).get(platform) or _default_platform_state()
    )
    platform_state["recent_failures"] = _trim_failures(
        config, [dict(item) for item in platform_state.get("recent_failures") or []]
    )
    platform_state["recent_failure_count"] = len(platform_state["recent_failures"])
    freeze = dict(platform_state.get("freeze") or {})
    until_dt = _parse_iso(str(freeze.get("until") or ""))
    if freeze.get("active") and until_dt and until_dt <= datetime.now(UTC):
        freeze = {
            "active": False,
            "reason": "",
            "since": "",
            "until": "",
            "trigger_failure_reason": "",
            "threshold": config.adapter_failure_threshold,
            "count": 0,
        }
        platform_state["freeze"] = freeze
        state["platforms"][platform] = platform_state
        save_adapter_state(config, state)
    else:
        platform_state["freeze"] = freeze
    return platform_state


def record_adapter_failure(
    config: AppConfig,
    platform: str,
    run_id: str,
    failure_reason: str,
    *,
    fixture_pack_path: str = "",
    message: str = "",
) -> dict[str, Any]:
    state = load_adapter_state(config)
    platform_state = get_adapter_runtime(config, platform)
    failures = [dict(item) for item in platform_state.get("recent_failures") or []]
    failures.append(
        {
            "timestamp": now_utc(),
            "run_id": run_id,
            "failure_reason": failure_reason,
            "fixture_pack_path": fixture_pack_path,
            "message": message,
        }
    )
    failures = _trim_failures(config, failures)
    freeze_active = len(failures) >= max(1, config.adapter_failure_threshold)
    freeze = {
        "active": freeze_active,
        "reason": "failure_threshold_reached" if freeze_active else "",
        "since": now_utc() if freeze_active else "",
        "until": (
            (datetime.now(UTC) + timedelta(minutes=config.adapter_freeze_minutes)).isoformat()
            if freeze_active
            else ""
        ),
        "trigger_failure_reason": failure_reason if freeze_active else "",
        "threshold": config.adapter_failure_threshold,
        "count": len(failures),
    }
    platform_state.update(
        {
            "recent_failures": failures,
            "recent_failure_count": len(failures),
            "last_failure_at": now_utc(),
            "last_failure_reason": failure_reason,
            "last_fixture_pack": fixture_pack_path,
            "last_run_id": run_id,
            "freeze": freeze,
        }
    )
    state["platforms"][platform] = platform_state
    save_adapter_state(config, state)
    return platform_state


def record_adapter_success(
    config: AppConfig, platform: str, run_id: str, *, final_url: str = ""
) -> dict[str, Any]:
    state = load_adapter_state(config)
    platform_state = get_adapter_runtime(config, platform)
    platform_state.update(
        {
            "recent_failures": [],
            "recent_failure_count": 0,
            "last_run_id": run_id,
            "last_success_at": now_utc(),
            "last_success_url": final_url,
            "freeze": {
                "active": False,
                "reason": "",
                "since": "",
                "until": "",
                "trigger_failure_reason": "",
                "threshold": config.adapter_failure_threshold,
                "count": 0,
            },
        }
    )
    state["platforms"][platform] = platform_state
    save_adapter_state(config, state)
    return platform_state


def clear_adapter_freeze(
    config: AppConfig, platform: str, actor: str = "operator"
) -> dict[str, Any]:
    state = load_adapter_state(config)
    platform_state = get_adapter_runtime(config, platform)
    platform_state["freeze"] = {
        "active": False,
        "reason": "cleared_by_operator",
        "since": "",
        "until": "",
        "trigger_failure_reason": "",
        "threshold": config.adapter_failure_threshold,
        "count": 0,
        "cleared_at": now_utc(),
        "cleared_by": actor,
    }
    state["platforms"][platform] = platform_state
    save_adapter_state(config, state)
    return platform_state
