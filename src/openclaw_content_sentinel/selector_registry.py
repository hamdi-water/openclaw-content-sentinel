from __future__ import annotations

from typing import Any, cast

from .config import AppConfig
from .utils import dump_json, ensure_dir, load_json


def default_selector_registry_payload(config: AppConfig) -> dict[str, Any]:
    return {
        "registry_version": config.selector_registry_version,
        "platforms": {
            "linkedin": {
                "compose_url": config.publish_url_linkedin,
                "composer_patterns": [
                    ["textbox", "post text"],
                    ["textbox", "text editor"],
                    ["start a post"],
                    ["textbox"],
                ],
                "submit_patterns": [
                    ["button", "post"],
                    ["button", "publish"],
                    ["button", "share"],
                ],
                "media_patterns": [
                    ["button", "media"],
                    ["button", "photo"],
                    ["button", "image"],
                ],
                "success_texts": [
                    "post shared",
                    "your post is now live",
                    "posted successfully",
                    "successfully posted",
                    "post successful",
                    "view post",
                ],
                "file_input_selector": "input[type=file]",
            },
            "facebook": {
                "compose_url": config.publish_url_facebook,
                "composer_patterns": [
                    ["button", "create a post"],
                    ["button", "créer une publication"],
                    ["button", "quoi de neuf"],
                    ["button", "what's on your mind"],
                    ["button", "what's up"],
                    ["button", "write something"],
                    ["region", "create a post"],
                    ["region", "créer une publication"],
                    ["textbox", "mind"],
                    ["textbox", "quoi de neuf"],
                    ["textbox", "write"],
                    ["textarea"],
                    ["textbox"],
                ],
                "submit_patterns": [
                    ["button", "post"],
                    ["button", "publier"],
                    ["button", "poster"],
                    ["button", "publish"],
                    ["button", "share"],
                ],
                "media_patterns": [
                    ["button", "photo/vidéo"],
                    ["button", "photo/video"],
                    ["button", "photo"],
                    ["button", "image"],
                    ["button", "media"],
                ],
                "success_texts": [
                    "your post is now published",
                    "posted to your profile",
                    "post published",
                    "posted successfully",
                    "publication",
                    "publiée",
                    "publié",
                ],
                "file_input_selector": "input[type=file]",
            },
            "x": {
                "compose_url": config.publish_url_x,
                "composer_patterns": [
                    ["textbox", "what's happening"],
                    ["textbox", "post text"],
                    ["textbox", "compose"],
                    ["textbox"],
                ],
                "submit_patterns": [
                    ["button", "post"],
                    ["button", "tweet"],
                    ["button", "publish"],
                ],
                "media_patterns": [
                    ["button", "media"],
                    ["button", "photo"],
                    ["button", "image"],
                ],
                "success_texts": [
                    "your post was sent",
                    "posted successfully",
                    "post published",
                    "successfully posted",
                ],
                "file_input_selector": "input[type=file]",
            },
        },
    }


def ensure_selector_registry_file(config: AppConfig) -> dict[str, Any]:
    payload = default_selector_registry_payload(config)
    if not config.selector_registry_file.exists():
        ensure_dir(config.selector_registry_file.parent)
        dump_json(config.selector_registry_file, payload)
    return payload


def _normalize_patterns(value: Any) -> tuple[tuple[str, ...], ...]:
    if not isinstance(value, list):
        return ()
    normalized: list[tuple[str, ...]] = []
    for item in value:
        if isinstance(item, (list, tuple)):
            pattern = tuple(str(part).strip().lower() for part in item if str(part).strip())
            if pattern:
                normalized.append(pattern)
        elif isinstance(item, str) and item.strip():
            normalized.append((item.strip().lower(),))
    return tuple(normalized)


def _normalize_strings(value: Any) -> tuple[str, ...]:
    if not isinstance(value, list):
        return ()
    items = [str(item).strip() for item in value if str(item).strip()]
    return tuple(items)


def load_selector_registry(config: AppConfig) -> dict[str, Any]:
    if not config.selector_registry_file.exists():
        payload = ensure_selector_registry_file(config)
    else:
        payload = load_json(config.selector_registry_file, default={}) or {}
    if not isinstance(payload, dict):
        payload = default_selector_registry_payload(config)
    platforms_payload = payload.get("platforms")
    normalized_platforms: dict[str, dict[str, Any]] = {}
    if isinstance(platforms_payload, dict):
        for platform, raw in platforms_payload.items():
            if not isinstance(raw, dict):
                continue
            normalized_platforms[str(platform).strip().lower()] = {
                "compose_url": str(raw.get("compose_url") or "").strip(),
                "composer_patterns": _normalize_patterns(raw.get("composer_patterns")),
                "submit_patterns": _normalize_patterns(raw.get("submit_patterns")),
                "media_patterns": _normalize_patterns(raw.get("media_patterns")),
                "success_texts": _normalize_strings(raw.get("success_texts")),
                "file_input_selector": str(raw.get("file_input_selector") or "").strip(),
            }
    registry_version = (
        str(payload.get("registry_version") or "").strip() or config.selector_registry_version
    )
    return {
        "path": str(config.selector_registry_file),
        "exists": config.selector_registry_file.exists(),
        "registry_version": registry_version,
        "platforms": normalized_platforms,
    }


def selector_override_for(config: AppConfig, platform: str) -> dict[str, Any]:
    registry = load_selector_registry(config)
    platforms = cast(dict[str, dict[str, Any]], registry.get("platforms") or {})
    return dict(platforms.get(platform, {}))
