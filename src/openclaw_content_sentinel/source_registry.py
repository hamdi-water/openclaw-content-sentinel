from __future__ import annotations

from typing import Any
from urllib.parse import urlparse

from .config import AppConfig
from .utils import dump_json, ensure_dir, load_json

DEFAULT_REGISTRY = {
    "sources": [
        {
            "source_id": "rss_public_feeds",
            "type": "rss",
            "domains": [],
            "acquisition": "rss",
            "priority": 10,
            "legal_notes": "Public feed; prefer canonical URL and attribution.",
        },
        {
            "source_id": "google_news_rss",
            "type": "public_news",
            "domains": ["news.google.com"],
            "acquisition": "rss",
            "priority": 9,
            "legal_notes": (
                "Use as trend discovery only; always resolve to publisher "
                "source before summarizing."
            ),
        },
        {
            "source_id": "searxng_public_search",
            "type": "searxng",
            "domains": [],
            "acquisition": "search",
            "priority": 7,
            "legal_notes": (
                "Public search discovery only; fetch original source for content analysis."
            ),
        },
        {
            "source_id": "pytrends_google_trends",
            "type": "pytrends",
            "domains": [],
            "acquisition": "pytrends",
            "priority": 8,
            "legal_notes": "Trend signal only; not a content source.",
        },
        {
            "source_id": "html_direct_competitor",
            "type": "html",
            "domains": [],
            "acquisition": "http_direct",
            "priority": 10,
            "legal_notes": (
                "Public page extraction only; respect robots/paywalls and attribute sources."
            ),
        },
        {
            "source_id": "browser_fallback_competitor",
            "type": "browser_fallback",
            "domains": [],
            "acquisition": "openclaw_browser",
            "priority": 6,
            "legal_notes": (
                "Use only when direct HTML is weak or JS-rendered; stop on access controls."
            ),
        },
    ]
}


def load_source_registry(config: AppConfig) -> dict[str, Any]:
    path = config.source_registry_file
    if not path.exists():
        ensure_dir(path.parent)
        dump_json(path, DEFAULT_REGISTRY)
    payload = load_json(path, default=DEFAULT_REGISTRY) or DEFAULT_REGISTRY
    sources = payload.get("sources")
    if not isinstance(sources, list):
        payload["sources"] = list(DEFAULT_REGISTRY["sources"])
    return payload


def is_domain_allowed(config: AppConfig, url: str) -> bool:
    """Phase 18: Strict domain whitelisting for content acquisition."""
    hostname = urlparse(url).netloc.lower().replace("www.", "")
    if not hostname:
        return False
    registry = load_source_registry(config)
    for item in registry.get("sources") or []:
        domains = [str(value).lower() for value in (item.get("domains") or [])]
        if hostname in domains:
            return True
    # Default behavior for discovery sources if they are empty lists
    discovery_types = {"search", "rss", "pytrends", "public_news"}
    for item in registry.get("sources") or []:
        if item.get("acquisition") in discovery_types and not item.get("domains"):
            return True
    return False


def resolve_source_entry(config: AppConfig, source_type: str, url: str = "") -> dict[str, Any]:
    registry = load_source_registry(config)
    hostname = urlparse(url).netloc.lower().replace("www.", "")
    for item in registry.get("sources") or []:
        if str(item.get("type") or "").strip().lower() != str(source_type or "").strip().lower():
            continue
        domains = [str(value).lower() for value in (item.get("domains") or [])]
        if domains and hostname and hostname not in domains:
            continue
        return dict(item)
    return {
        "source_id": f"unregistered_{source_type}",
        "type": source_type,
        "domains": [hostname] if hostname else [],
        "acquisition": "unknown",
        "priority": 0,
        "legal_notes": "No explicit registry entry found. Domain not explicitly whitelisted.",
    }


def build_source_registry_report(config: AppConfig) -> dict[str, Any]:
    registry = load_source_registry(config)
    return {
        "path": str(config.source_registry_file),
        "count": len(registry.get("sources") or []),
        "items": registry.get("sources") or [],
    }


def write_source_registry(config: AppConfig, payload: dict[str, Any]) -> dict[str, Any]:
    """Ref §18.2: Save the source registry to disk."""
    path = config.source_registry_file
    ensure_dir(path.parent)
    dump_json(path, payload)
    return payload
