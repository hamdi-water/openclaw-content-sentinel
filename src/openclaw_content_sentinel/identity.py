from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any
from urllib.parse import urlparse, urlunparse


def normalize_whitespace(value: str) -> str:
    return re.sub(r"\s+", " ", str(value or "").strip())


def normalize_text_for_hash(value: str) -> str:
    return normalize_whitespace(value).lower()


def sha256_text(value: str) -> str:
    return hashlib.sha256(str(value or "").encode("utf-8")).hexdigest()


def sha256_json(payload: object) -> str:
    serialized = json.dumps(payload, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
    return sha256_text(serialized)


def canonicalize_url(url: str) -> str:
    raw = str(url or "").strip()
    if not raw:
        return ""
    parsed = urlparse(raw)
    netloc = parsed.netloc.lower().replace(":80", "").replace(":443", "")
    path = parsed.path or "/"
    if path != "/":
        path = path.rstrip("/")
    return urlunparse((parsed.scheme.lower() or "https", netloc, path, "", parsed.query, ""))


def hash_url(url: str) -> str:
    return sha256_text(canonicalize_url(url))


def file_sha256(path: Path) -> str:
    if not path.exists() or not path.is_file():
        return ""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def build_run_identity(run: dict[str, Any]) -> dict[str, Any]:
    competitor_url = str(run.get("competitor_url") or "")
    article = run.get("article") or {}
    canonical_url = str(article.get("canonical_url") or article.get("url") or competitor_url or "")
    prompt = str(run.get("prompt") or "")
    keywords = list(run.get("keywords") or [])
    targets = list(run.get("platform_targets") or [])
    input_hash = sha256_json(
        {
            "prompt": normalize_text_for_hash(prompt),
            "competitor_url": canonicalize_url(competitor_url),
            "keywords": [normalize_text_for_hash(item) for item in keywords],
            "targets": targets,
        }
    )
    return {
        "run_id": str(run.get("run_id") or ""),
        "trigger_kind": str(run.get("trigger_kind") or "manual"),
        "input_hash": input_hash,
        "prompt_hash": sha256_text(normalize_text_for_hash(prompt)),
        "competitor_url_hash": hash_url(competitor_url),
        "canonical_url_hash": hash_url(canonical_url),
        "content_hash": str(article.get("content_hash") or ""),
        "raw_html_hash": str(article.get("raw_html_hash") or ""),
    }


def build_publish_identity(run: dict[str, Any], platform: str, draft_text: str) -> dict[str, Any]:
    normalized_text = normalize_text_for_hash(draft_text)
    publish_target_hash = sha256_json(
        {
            "run_id": str(run.get("run_id") or ""),
            "platform": platform,
            "adapter": str(run.get("publish_adapter_version") or ""),
        }
    )
    canonical_post_hash = sha256_json(
        {
            "platform": platform,
            "text": normalized_text,
            "image_backend": str((run.get("cost_proof") or {}).get("image_backend") or ""),
            "publish_adapter_version": str(run.get("publish_adapter_version") or ""),
        }
    )
    idempotency_key = sha256_json(
        {
            "platform": platform,
            "publish_target_hash": publish_target_hash,
            "canonical_post_hash": canonical_post_hash,
            "adapter": str(run.get("publish_adapter_version") or ""),
            "approval_policy": str(run.get("approval_policy") or ""),
        }
    )
    return {
        "publish_target_hash": publish_target_hash,
        "canonical_post_hash": canonical_post_hash,
        "idempotency_key": idempotency_key,
        "draft_text_hash": sha256_text(normalized_text),
    }


def build_proof_id(
    run_id: str, platform: str, canonical_post_hash: str, final_url: str = ""
) -> str:
    return sha256_json(
        {
            "run_id": run_id,
            "platform": platform,
            "canonical_post_hash": canonical_post_hash,
            "final_url": canonicalize_url(final_url),
        }
    )
