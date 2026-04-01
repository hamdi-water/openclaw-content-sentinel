# ruff: noqa: E501, C901
from __future__ import annotations

import json
import shutil
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, cast
from zoneinfo import ZoneInfo

from .analytics import update_run_analytics
from .competitor import extract_article
from .compliance import log_operator_action
from .config import AppConfig
from .drafting import (
    build_article_draft,
    build_facebook_draft,
    build_image_prompt,
    build_linkedin_draft,
    build_original_angle,
    build_x_draft,
)
from .editorial import (
    analyze_source_safety,
    evaluate_run_quality,
    load_brand_profile_text,
    render_source_safety_report,
    similarity_ratio,
    summarize_brand_profile,
)
from .graph_store import (
    backend_status as graph_backend_status,
)
from .graph_store import (
    load_graph_context as load_knowledge_graph_context,
)
from .graph_store import (
    sync_run_graph,
)
from .identity import (
    build_proof_id,
    build_publish_identity,
    build_run_identity,
    file_sha256,
)
from .images import build_social_card
from .memory import backend_status, humanize_memory_hit, search_memory, sync_run_memory
from .models import CompetitorArticle, IdentityRecord, RunRecord
from .recovery import auto_recover
from .research import (
    curate_trend_signals,
    derive_keywords,
    extract_context_terms,
    fetch_google_trends_signals,
    fetch_public_news_signals,
    fetch_rss_signals,
    prompt_is_actionable,
    query_searx,
)
from .security import load_openclaw_runtime_config
from .selector_registry import ensure_selector_registry_file
from .source_registry import resolve_source_entry
from .storage import RunStore
from .telegram_notify import send_run_preview
from .utils import (
    AdaptiveTimeout,
    CircuitBreaker,
    LatencyTracker,
    dump_json,
    load_json,
    now_utc,
    read_text,
    slugify,
)

try:
    import langgraph  # noqa: F401
    langgraph_supported = True
except ImportError:
    langgraph_supported = False


def bootstrap_workspace(config: AppConfig) -> None:
    store = RunStore(config)
    store.bootstrap()
    ensure_selector_registry_file(config)
    # Â§21.2: Automated SRE recovery on boot
    auto_recover(config)


def _timestamp_run(run: dict[str, Any], stage: str) -> dict[str, Any]:
    run.setdefault("stage_timestamps", {})[stage] = now_utc()
    return run


def _confidence_value(run: dict[str, Any]) -> float:
    try:
        return float(run.get("confidence", 0) or 0.0)
    except (TypeError, ValueError):
        return 0.0


def _analysis_payload(run: dict[str, Any]) -> dict[str, Any]:
    return cast(dict[str, Any], run.setdefault("analysis", {}))


def _quality_gate_payload(run: dict[str, Any]) -> dict[str, Any]:
    return cast(dict[str, Any], run.setdefault("quality_gate", {}))


def _refresh_analysis_runtime_payloads(config: AppConfig, run: dict[str, Any]) -> None:
    analysis = _analysis_payload(run)
    analysis["proof_readiness"] = build_proof_readiness_payload(run)
    analysis["delivery_pipeline"] = build_delivery_pipeline_payload(config, run)


def _parse_iso(value: str) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _normalize_list_field(value: object) -> list[str]:
    if isinstance(value, list):
        items = value
    elif isinstance(value, str):
        items = [part.strip(" -\t") for part in value.replace("\r", "\n").split("\n")]
    else:
        items = []
    normalized = []
    seen: set[str] = set()
    for item in items:
        text = str(item).strip()
        if not text:
            continue
        lowered = text.lower()
        if lowered in seen:
            continue
        seen.add(lowered)
        normalized.append(text)
    return normalized


def _draft_path(store: RunStore, run_id: str, platform: str) -> Path:
    return store.run_dir(run_id) / "drafts" / f"{platform}.md"


def _draft_text(store: RunStore, run_id: str, platform: str) -> str:
    path = _draft_path(store, run_id, platform)
    return read_text(path) if path.exists() else ""


def _proof_pack_for_run(config: AppConfig, run: dict[str, Any]) -> dict[str, Any]:
    store = RunStore(config)
    run_id = str(run.get("run_id") or "")
    outputs = run.get("outputs") or {}
    img_asset = str(outputs.get("image_asset") or "media/social_card.png")
    image_path = store.run_dir(run_id) / img_asset
    image_hash = file_sha256(image_path) if image_path.exists() else ""
    results = {}
    for platform in run.get("platform_targets") or []:
        draft_text = _draft_text(store, run_id, platform)
        publish_identity = build_publish_identity(run, platform, draft_text)
        post_result = (run.get("post_results") or {}).get(platform) or {}
        screenshots = [
            str(item) for item in (post_result.get("screenshots") or []) if str(item).strip()
        ]
        screenshot_hashes = []
        for screenshot in screenshots:
            candidate = store.run_dir(run_id) / screenshot
            screenshot_hashes.append(
                {
                    "path": screenshot,
                    "sha256": file_sha256(candidate) if candidate.exists() else "",
                }
            )
        final_url = str(post_result.get("url") or "").strip()
        results[platform] = {
            **publish_identity,
            "proof_id": build_proof_id(
                run_id,
                platform,
                publish_identity["canonical_post_hash"],
                final_url=final_url,
            ),
            "status": str(post_result.get("status") or "pending"),
            "final_url": final_url,
            "failure_reason": str(post_result.get("failure_reason") or ""),
            "screenshot_hashes": screenshot_hashes,
            "published_text_hash": publish_identity["draft_text_hash"],
            "image_asset_hash": image_hash,
            "adapter_version": str(run.get("publish_adapter_version") or ""),
        }
    return cast(
        dict[str, Any],
        {
            "run_id": run_id,
            "retention_days_proofs": config.retention_days_proofs,
            "results": results,
        },
    )


def _daily_input_fields(payload: dict[str, Any]) -> dict[str, Any]:
    return {
        "topic": str(payload.get("topic") or "").strip(),
        "business_angle": str(payload.get("business_angle") or "").strip(),
        "target_audience": str(payload.get("target_audience") or "").strip(),
        "key_call_to_action": str(payload.get("key_call_to_action") or "").strip(),
        "do_not_say": _normalize_list_field(payload.get("do_not_say") or []),
        "mandatory_references": _normalize_list_field(payload.get("mandatory_references") or []),
    }


def _build_prompt_from_daily_fields(fields: dict[str, Any]) -> str:
    required = [
        fields.get("topic", ""),
        fields.get("business_angle", ""),
        fields.get("target_audience", ""),
        fields.get("key_call_to_action", ""),
    ]
    if not all(required):
        return ""
    lines = [
        "Write an original operator-facing response article and platform-native social drafts.",
        f"Topic: {fields['topic']}",
        f"Business angle: {fields['business_angle']}",
        f"Target audience: {fields['target_audience']}",
        f"Key call to action: {fields['key_call_to_action']}",
    ]
    if fields.get("do_not_say"):
        lines.append("Do not say: " + "; ".join(fields["do_not_say"]))
    if fields.get("mandatory_references"):
        lines.append("Mandatory references: " + "; ".join(fields["mandatory_references"]))
    return "\n".join(lines).strip()


def normalize_daily_input_payload(
    payload: dict[str, Any], default_targets: tuple[str, ...]
) -> dict[str, Any]:
    raw_prompt = str(payload.get("prompt") or "").strip()
    fields = _daily_input_fields(payload)
    structured_prompt = _build_prompt_from_daily_fields(fields)
    prompt = raw_prompt
    if (not prompt or not prompt_is_actionable(prompt)) and structured_prompt:
        prompt = structured_prompt
    structured_complete = all(
        [
            fields.get("topic"),
            fields.get("business_angle"),
            fields.get("target_audience"),
            fields.get("key_call_to_action"),
        ]
    )
    return {
        "prompt": prompt,
        "prompt_source": (
            "structured_fields"
            if prompt == structured_prompt and structured_prompt
            else ("raw_prompt" if raw_prompt else "missing")
        ),
        "competitor_url": str(payload.get("competitor_url") or "").strip(),
        "keywords": list(payload.get("keywords") or []),
        "targets": list(payload.get("targets") or default_targets),
        "updated_at": payload.get("updated_at", ""),
        "topic": fields["topic"],
        "business_angle": fields["business_angle"],
        "target_audience": fields["target_audience"],
        "key_call_to_action": fields["key_call_to_action"],
        "do_not_say": fields["do_not_say"],
        "mandatory_references": fields["mandatory_references"],
        "structured_complete": structured_complete,
    }


def load_daily_brief_queue(config: AppConfig) -> dict[str, Any]:
    payload = load_json(config.daily_brief_queue_file, default={"briefs": []}) or {}
    briefs = payload.get("briefs")
    normalized_briefs = []
    now = datetime.now(UTC)
    if isinstance(briefs, list):
        for index, item in enumerate(briefs, start=1):
            if not isinstance(item, dict):
                continue
            normalized = normalize_daily_input_payload(item, config.default_targets)
            due_at = str(item.get("due_at") or "").strip()
            due_dt = _parse_iso(due_at) if due_at else None
            overdue = bool(due_dt and due_dt < now)
            normalized.update(
                {
                    "brief_id": str(
                        item.get("brief_id") or item.get("id") or f"brief-{index:02d}"
                    ).strip()
                    or f"brief-{index:02d}",
                    "label": str(
                        item.get("label")
                        or item.get("topic")
                        or item.get("prompt")
                        or f"brief-{index:02d}"
                    ).strip(),
                    "status": str(item.get("status") or "ready").strip().lower() or "ready",
                    "enabled": bool(item.get("enabled", True)),
                    "priority": int(item.get("priority") or 0),
                    "due_at": due_at,
                    "overdue": overdue,
                    "queue_index": index - 1,
                    "source_file": str(config.daily_brief_queue_file),
                }
            )
            normalized_briefs.append(normalized)
    normalized_briefs.sort(
        key=lambda item: (
            0 if item.get("overdue") else 1,
            str(item.get("due_at") or "9999-12-31T23:59:59+00:00"),
            -int(item.get("priority") or 0),
            str(item.get("updated_at") or ""),
            str(item.get("brief_id") or ""),
        )
    )
    ready_briefs = [
        item
        for item in normalized_briefs
        if item.get("enabled", True)
        and str(item.get("status") or "").strip().lower() in {"ready", "queued", "approved"}
    ]
    return {
        "path": str(config.daily_brief_queue_file),
        "briefs": normalized_briefs,
        "counts": {
            "total": len(normalized_briefs),
            "ready": len(ready_briefs),
            "overdue_ready": sum(1 for item in ready_briefs if item.get("overdue")),
        },
    }


def save_daily_brief(
    config: AppConfig,
    competitor_url: str,
    *,
    topic: str = "",
    business_angle: str = "",
    target_audience: str = "",
    key_call_to_action: str = "",
    prompt: str = "",
    keywords: list[str] | None = None,
    targets: list[str] | None = None,
    do_not_say: list[str] | None = None,
    mandatory_references: list[str] | None = None,
    label: str = "",
    priority: int = 0,
    status: str = "ready",
    enabled: bool = True,
    due_at: str = "",
) -> dict[str, Any]:
    queue = load_json(config.daily_brief_queue_file, default={"briefs": []}) or {}
    briefs = queue.get("briefs")
    if not isinstance(briefs, list):
        briefs = []
    index = len(briefs) + 1
    brief_id = f"brief-{index:02d}"
    payload = {
        "brief_id": brief_id,
        "label": label.strip() or topic.strip() or brief_id,
        "status": status.strip().lower() or "ready",
        "priority": int(priority),
        "enabled": bool(enabled),
        "due_at": due_at.strip(),
        "prompt": prompt.strip(),
        "competitor_url": competitor_url.strip(),
        "keywords": list(keywords or []),
        "targets": list(targets or config.default_targets),
        "topic": topic.strip(),
        "business_angle": business_angle.strip(),
        "target_audience": target_audience.strip(),
        "key_call_to_action": key_call_to_action.strip(),
        "do_not_say": list(do_not_say or []),
        "mandatory_references": list(mandatory_references or []),
        "updated_at": now_utc(),
    }
    briefs.append(payload)
    dump_json(
        config.daily_brief_queue_file,
        {
            **queue,
            "briefs": briefs,
            "updated_at": now_utc(),
        },
    )
    return payload


def update_daily_brief(
    config: AppConfig,
    brief_id: str,
    *,
    status: str | None = None,
    enabled: bool | None = None,
    priority: int | None = None,
    due_at: str | None = None,
) -> dict[str, Any]:
    queue = load_json(config.daily_brief_queue_file, default={"briefs": []}) or {}
    briefs = queue.get("briefs")
    if not isinstance(briefs, list):
        briefs = []
    updated_payload = {}
    for item in briefs:
        if not isinstance(item, dict):
            continue
        if str(item.get("brief_id") or item.get("id") or "").strip() != brief_id:
            continue
        if status is not None:
            item["status"] = status.strip().lower()
        if enabled is not None:
            item["enabled"] = bool(enabled)
        if priority is not None:
            item["priority"] = int(priority)
        if due_at is not None:
            item["due_at"] = due_at.strip()
        item["updated_at"] = now_utc()
        updated_payload = dict(item)
        break
    if updated_payload:
        dump_json(
            config.daily_brief_queue_file,
            {
                **queue,
                "briefs": briefs,
                "updated_at": now_utc(),
            },
        )
    return updated_payload


def promote_daily_brief(config: AppConfig, brief_id: str) -> dict[str, Any]:
    queue = load_daily_brief_queue(config)
    selected = next((item for item in queue["briefs"] if item.get("brief_id") == brief_id), None)
    if not selected:
        raise FileNotFoundError(
            f"Daily brief {brief_id} was not found in {config.daily_brief_queue_file}."
        )
    payload = {
        "prompt": str(selected.get("prompt") or "").strip(),
        "competitor_url": str(selected.get("competitor_url") or "").strip(),
        "keywords": list(selected.get("keywords") or []),
        "targets": list(selected.get("targets") or config.default_targets),
        "topic": str(selected.get("topic") or "").strip(),
        "business_angle": str(selected.get("business_angle") or "").strip(),
        "target_audience": str(selected.get("target_audience") or "").strip(),
        "key_call_to_action": str(selected.get("key_call_to_action") or "").strip(),
        "do_not_say": list(selected.get("do_not_say") or []),
        "mandatory_references": list(selected.get("mandatory_references") or []),
        "updated_at": now_utc(),
    }
    dump_json(config.daily_input_file, payload)
    update_daily_brief(config, brief_id, status="promoted")
    return {
        "brief_id": brief_id,
        "daily_input_path": str(config.daily_input_file),
        "resolved_prompt_actionable": prompt_is_actionable(
            normalize_daily_input_payload(payload, config.default_targets).get("prompt", "")
        ),
    }


def _select_daily_brief(config: AppConfig) -> dict[str, Any]:
    queue = load_daily_brief_queue(config)
    for item in queue["briefs"]:
        if not item.get("enabled", True):
            continue
        if str(item.get("status") or "").strip().lower() not in {"ready", "queued", "approved"}:
            continue
        if not str(item.get("competitor_url") or "").strip():
            continue
        if not prompt_is_actionable(str(item.get("prompt") or "").strip()):
            continue
        resolved = dict(item)
        resolved["prompt_source"] = f"brief_queue:{item.get('brief_id', '')}"
        resolved["resolved_from"] = "daily_brief_queue"
        resolved["selected_via_queue"] = True
        return resolved
    return {}


def consume_daily_brief(config: AppConfig, brief_id: str, run_id: str = "") -> dict[str, Any]:
    queue = load_json(config.daily_brief_queue_file, default={"briefs": []}) or {}
    briefs = queue.get("briefs")
    if not isinstance(briefs, list):
        briefs = []
    updated = False
    for item in briefs:
        if not isinstance(item, dict):
            continue
        candidate_id = str(item.get("brief_id") or item.get("id") or "").strip()
        if candidate_id != brief_id:
            continue
        item["status"] = "consumed"
        item["last_run_id"] = run_id
        item["consumed_at"] = now_utc()
        updated = True
        break
    if updated:
        payload = {
            **queue,
            "briefs": briefs,
            "updated_at": now_utc(),
        }
        dump_json(config.daily_brief_queue_file, payload)
        return payload
    return queue


def _portfolio_diversity_analysis(
    config: AppConfig, run: dict[str, Any], lookback_days: int = 14
) -> dict[str, Any]:
    store = RunStore(config)
    now = _parse_iso(run.get("created_at", "")) or datetime.now(UTC)
    cutoff = now - timedelta(days=max(1, lookback_days))
    current_keywords = {item.lower() for item in (run.get("keywords") or []) if str(item).strip()}
    current_prompt = str(run.get("prompt") or "").strip()
    candidates = []
    for other in store.list_runs():
        if other.get("run_id") == run.get("run_id"):
            continue
        if (other.get("simulation") or {}).get("enabled"):
            continue
        other_status = str(other.get("status") or "").strip().lower()
        other_approval = str(other.get("approval_state") or "").strip().lower()
        if other_approval != "approved" and other_status not in {
            "approved",
            "posting",
            "posting_failed",
            "posted",
        }:
            continue
        created_at = _parse_iso(other.get("created_at", ""))
        if created_at and created_at < cutoff:
            continue
        other_keywords = {
            item.lower() for item in (other.get("keywords") or []) if str(item).strip()
        }
        keyword_overlap = 0.0
        if current_keywords or other_keywords:
            union = current_keywords | other_keywords
            keyword_overlap = len(current_keywords & other_keywords) / max(1, len(union))
        prompt_similarity = similarity_ratio(current_prompt, str(other.get("prompt") or ""))
        draft_overlap = similarity_ratio(
            str((run.get("analysis") or {}).get("original_angle") or ""),
            str((other.get("analysis") or {}).get("original_angle") or ""),
        )
        composite = round(
            (prompt_similarity * 0.45) + (keyword_overlap * 0.35) + (draft_overlap * 0.20), 4
        )
        repetition_risk = round(max(composite, prompt_similarity, keyword_overlap), 4)
        if composite < 0.2 and keyword_overlap < 0.3:
            continue
        candidates.append(
            {
                "run_id": other.get("run_id", ""),
                "status": other_status,
                "approval_state": other_approval,
                "keyword_overlap_ratio": round(keyword_overlap, 4),
                "prompt_similarity_ratio": prompt_similarity,
                "angle_similarity_ratio": draft_overlap,
                "composite_similarity": composite,
                "repetition_risk_score": repetition_risk,
            }
        )
    candidates.sort(
        key=lambda item: (item["repetition_risk_score"], item["composite_similarity"]), reverse=True
    )
    top = candidates[:5]
    max_similarity = max((item["repetition_risk_score"] for item in top), default=0.0)
    return {
        "lookback_days": lookback_days,
        "recent_similar_runs": top,
        "recent_similarity_max": max_similarity,
        "recent_similarity_flag": max_similarity >= 0.75,
    }


def create_run(
    config: AppConfig,
    prompt: str,
    competitor_url: str,
    keywords: list[str] | None = None,
    targets: list[str] | None = None,
    target_language: str = "fr",  # Â§7: Language context
    created_at_override: str = "",
    simulation: dict[str, Any] | None = None,
    daily_input: dict[str, Any] | None = None,
    trigger_kind: str = "manual",
    persona_id: str = "default",
) -> dict[str, Any]:
    bootstrap_workspace(config)
    store = RunStore(config)
    control = store.load_control()
    created_at = created_at_override or now_utc()
    base_slug = slugify(prompt or competitor_url)
    run_stamp = (
        created_at[11:19].replace(":", "")
        if "T" in created_at
        else now_utc()[11:19].replace(":", "")
    )
    import random
    import string

    suffix = "".join(random.choices(string.ascii_lowercase + string.digits, k=4))  # noqa: S311
    run_id = f"{created_at[:10]}-{run_stamp}-{suffix}-{base_slug}"
    record = RunRecord(
        run_id=run_id,
        created_at=created_at,
        updated_at=created_at,
        prompt=prompt.strip(),
        competitor_url=competitor_url.strip(),
        keywords=list(keywords or []),
        platform_targets=list(targets or config.default_targets),
        status="created",
        approval_state="pending",
        schema_version=config.schema_version,
        publish_adapter_version=config.publish_adapter_version,
        prompt_bundle_version=config.prompt_bundle_version,
        retrieval_policy_version=config.retrieval_policy_version,
        scoring_policy_version=config.scoring_policy_version,
        approval_policy=config.approval_policy,
        trigger_kind=trigger_kind,
        telegram_route=config.telegram_target or "",
        automation_paused_snapshot=bool(control.get("automation_paused", False)),
        daily_input=daily_input or {},
        stage_timestamps={"created": created_at},
        cost_proof={
            "free_backends_used": [],
            "image_backend": "procedural_local"
            if config.image_backend == "procedural"
            else config.image_backend,
            "llm_surface": config.preferred_model or "set-in-openclaw",
            "paid_services_used": [],
        },
        identity=IdentityRecord(
            worker_id=config.worker_id,
            persona_id=persona_id,
        ),
        simulation=simulation or {},
    )
    payload = record.to_dict()
    cast(dict[str, Any], payload)["identity"] = build_run_identity(payload)
    payload["proof_pack"] = _proof_pack_for_run(config, payload)
    payload["analysis"] = {
        **dict(payload.get("analysis") or {}),
        "delivery_pipeline": build_delivery_pipeline_payload(config, payload),
    }
    store.save_run(payload)

    # Â§29: Compliance logging
    log_operator_action(config, action="run_created", run_id=payload["run_id"], operator=trigger_kind)

    return payload


def ingest_competitor(config: AppConfig, run_id: str) -> dict[str, Any]:
    """Ref Â§29.2: Source ingestion/canonicalization."""
    store = RunStore(config)
    run = store.load_run(run_id)

    # Â§27.11: Use CircuitBreaker for ingestion
    cb = CircuitBreaker(failure_threshold=2)
    if not cb.can_execute():
        run["status"] = "failed"
        run["notes"].append("Circuit breaker open for ingestion.")
        store.save_run(run)
        return run

    try:
        article = extract_article(str(run["competitor_url"]), config)
        cb.record_success()
    except Exception as e:
        cb.record_failure()
        raise e

    if not run.get("keywords"):
        run["keywords"] = derive_keywords(str(run["prompt"]), article.title, article.summary)

    run["article"] = cast(dict[str, Any], article.to_dict())
    run["identity"] = cast(dict[str, Any], build_run_identity(run))
    run["analysis"] = {
        **dict(run.get("analysis") or {}),
        "source_registry": {
            "competitor": resolve_source_entry(
                config,
                "browser_fallback"
                if article.raw_html_hash and len(article.clean_text) < 1200
                else "html",
                article.canonical_url or article.url,
            ),
            "registry_path": str(config.source_registry_file),
        },
        "source_safety": analyze_source_safety(run["article"]),
        "delivery_pipeline": build_delivery_pipeline_payload(config, run),
    }
    run["proof_pack"] = _proof_pack_for_run(config, run)
    run["status"] = "ingested"
    _timestamp_run(run, "ingested")
    run["analysis"]["delivery_pipeline"] = build_delivery_pipeline_payload(config, run)
    run["updated_at"] = now_utc()
    store.save_run(run)
    store.save_text_artifact(run_id, "artifacts/source_summary.md", render_source_summary(article))
    store.save_text_artifact(run_id, "artifacts/source_safety.md", render_source_safety_report(run))
    return run


def research_trends(config: AppConfig, run_id: str) -> dict[str, Any]:
    store = RunStore(config)
    run = store.load_run(run_id)
    article = run.get("article") or {}
    article_title = article.get("title", "")
    keywords = run.get("keywords") or derive_keywords(
        run["prompt"], article_title, article.get("summary", "")
    )
    run["keywords"] = keywords
    context_terms = extract_context_terms(
        run.get("prompt", ""), article_title, article.get("summary", ""), limit=10
    )
    # Â§27.13: Use AdaptiveTimeout for research
    timer = AdaptiveTimeout(total_budget_ms=60000.0)
    timer.get_remaining_timeout_ms(default_ms=30000.0)

    rss_signals = fetch_rss_signals(config, keywords)
    public_news_signals = fetch_public_news_signals(config, keywords)
    trends_signals = fetch_google_trends_signals(config, keywords)
    searx_signals = query_searx(config, keywords)
    combined = rss_signals + public_news_signals + trends_signals + searx_signals

    # Â§26.10: Language prioritization
    target_lang = run.get("inputs", {}).get("target_language", "fr")
    from .rag import SimhashDeduplicator
    deduper = SimhashDeduplicator()

    filtered = []
    for sig in combined:
        # Filter by language if detected
        if hasattr(sig, "text") and sig.text:
            sig_lang = getattr(sig, "lang", None)
            if sig_lang and sig_lang != target_lang:
                continue
            # Â§30.9: SimHash deduplication
            if deduper.is_duplicate(sig.text):
                continue
            deduper.register(sig.text, getattr(sig, "id", str(hash(sig.text))))
        filtered.append(sig)

    curated = curate_trend_signals(filtered, context_terms=context_terms, limit=18)
    run["trend_signals"] = [item.to_dict() for item in curated]
    run["analysis"] = {
        **dict(run.get("analysis") or {}),
        "trend_research": {
            "context_terms": context_terms,
            "raw_signal_count": len(combined),
            "curated_signal_count": len(curated),
            "discarded_signal_count": max(0, len(combined) - len(curated)),
            "sources": {
                "rss": len(rss_signals),
                "public_news": len(public_news_signals),
                "pytrends": len(trends_signals),
                "searxng": len(searx_signals),
            },
        },
        "source_registry": {
            **dict((run.get("analysis") or {}).get("source_registry") or {}),
            "trend_sources": [
                resolve_source_entry(config, source_type)
                for source_type, count in {
                    "rss": len(rss_signals),
                    "public_news": len(public_news_signals),
                    "pytrends": len(trends_signals),
                    "searxng": len(searx_signals),
                }.items()
                if count
            ],
            "registry_path": str(config.source_registry_file),
        },
        "delivery_pipeline": build_delivery_pipeline_payload(config, run),
    }
    run["identity"] = cast(dict[str, Any], build_run_identity(run))
    run["proof_pack"] = _proof_pack_for_run(config, run)
    run["status"] = "researched"
    _timestamp_run(run, "researched")
    run["analysis"]["delivery_pipeline"] = build_delivery_pipeline_payload(config, run)

    backends = ["rss"]
    if public_news_signals:
        backends.append("public_news")
    if trends_signals:
        backends.append("pytrends")
    if searx_signals:
        backends.append("searxng")
    run.setdefault("cost_proof", {})["free_backends_used"] = sorted(set(backends))

    run["updated_at"] = now_utc()
    store.save_run(run)
    store.save_text_artifact(run_id, "artifacts/trend_summary.md", render_trend_summary(run))
    return run


def load_memory_context(config: AppConfig, run_id: str) -> dict[str, Any]:
    store = RunStore(config)
    run = store.load_run(run_id)
    query = " ".join([run.get("prompt", ""), *list(run.get("keywords") or [])]).strip()
    hits = search_memory(config, query, top_k=5)
    run["memory_hits"] = hits
    run["analysis"] = {
        **dict(run.get("analysis") or {}),
        "original_angle": build_original_angle(run, hits),
        "delivery_pipeline": build_delivery_pipeline_payload(config, run),
    }
    _timestamp_run(run, "memory_loaded")
    run["analysis"]["delivery_pipeline"] = build_delivery_pipeline_payload(config, run)
    run["updated_at"] = now_utc()
    store.save_run(run)
    store.save_text_artifact(run_id, "artifacts/memory_context.md", render_memory_context(run))
    return run


def load_graph_context(config: AppConfig, run_id: str) -> dict[str, Any]:
    store = RunStore(config)
    run = load_knowledge_graph_context(config, run_id)
    run["analysis"] = {
        **dict(run.get("analysis") or {}),
        "delivery_pipeline": build_delivery_pipeline_payload(config, run),
    }
    run["updated_at"] = now_utc()
    store.save_run(run)
    graph_context_path = (run.get("outputs") or {}).get(
        "graph_context", "artifacts/graph_context.md"
    )
    store.save_text_artifact(run_id, graph_context_path, render_graph_context(run))
    return run


def _dedupe_strings(items: list[str], limit: int = 12) -> list[str]:
    deduped = []
    seen: set[str] = set()
    for item in items:
        text = str(item).strip()
        if not text:
            continue
        lowered = text.lower()
        if lowered in seen:
            continue
        seen.add(lowered)
        deduped.append(text)
        if len(deduped) >= limit:
            break
    return deduped


def build_hybrid_context_payload(run: dict[str, Any]) -> dict[str, Any]:
    daily_input = run.get("daily_input") or {}
    memory_titles = _dedupe_strings(
        [humanize_memory_hit(item) for item in (run.get("memory_hits") or [])], limit=6
    )
    graph_entities = _dedupe_strings(
        [
            str(item.get("label") or item.get("id") or "").strip()
            for item in (run.get("graph_hits") or [])
        ],
        limit=8,
    )
    trend_titles = _dedupe_strings(
        [str(item.get("title") or "").strip() for item in (run.get("trend_signals") or [])],
        limit=8,
    )
    strategic_themes = _dedupe_strings(
        [
            str(daily_input.get("topic") or ""),
            str(daily_input.get("business_angle") or ""),
            *list(run.get("keywords") or []),
            *trend_titles,
            *graph_entities,
            *memory_titles,
        ],
        limit=10,
    )
    differentiation_guardrails = []
    original_angle = str((run.get("analysis") or {}).get("original_angle") or "").strip()
    if original_angle:
        differentiation_guardrails.append(original_angle)
    for item in ((run.get("analysis") or {}).get("portfolio_diversity") or {}).get(
        "recent_similar_runs"
    ) or []:
        differentiation_guardrails.append(
            f"Avoid repeating {item.get('run_id', '')}; similarity risk={item.get('repetition_risk_score', item.get('composite_similarity', 0))}"
        )
    differentiation_guardrails = _dedupe_strings(differentiation_guardrails, limit=5)
    reference_guardrails = _dedupe_strings(
        [
            "Attribute the competitor source when responding publicly.",
            *list(daily_input.get("mandatory_references") or []),
        ],
        limit=6,
    )
    forbidden_phrases = _dedupe_strings(list(daily_input.get("do_not_say") or []), limit=8)
    return {
        "topic": str(daily_input.get("topic") or "").strip(),
        "business_angle": str(daily_input.get("business_angle") or "").strip(),
        "target_audience": str(daily_input.get("target_audience") or "").strip(),
        "key_call_to_action": str(daily_input.get("key_call_to_action") or "").strip(),
        "memory_titles": memory_titles,
        "graph_entities": graph_entities,
        "trend_titles": trend_titles,
        "strategic_themes": strategic_themes,
        "reference_guardrails": reference_guardrails,
        "forbidden_phrases": forbidden_phrases,
        "differentiation_guardrails": differentiation_guardrails,
    }


def render_researcher_report(run: dict[str, Any]) -> str:
    article = run.get("article") or {}
    trend_research = (run.get("analysis") or {}).get("trend_research") or {}
    source_safety = (run.get("analysis") or {}).get("source_safety") or {}
    lines = [
        "# Researcher Agent Report",
        "",
        f"Run ID: {run.get('run_id', '')}",
        f"Competitor title: {article.get('title', '')}",
        f"Keywords: {', '.join(run.get('keywords') or []) or 'None'}",
        f"Source safety severity: {source_safety.get('severity', 'unknown')}",
        "Trend sources: "
        + ", ".join(
            [
                f"rss={trend_research.get('sources', {}).get('rss', 0)}",
                f"public_news={trend_research.get('sources', {}).get('public_news', 0)}",
                f"pytrends={trend_research.get('sources', {}).get('pytrends', 0)}",
                f"searxng={trend_research.get('sources', {}).get('searxng', 0)}",
            ]
        ),
        f"Context terms: {', '.join(trend_research.get('context_terms') or []) or 'None'}",
        "",
        "Top trend signals:",
    ]
    signals = run.get("trend_signals") or []
    if signals:
        lines.extend(
            f"- {item.get('title', '')} ({item.get('source', '')})" for item in signals[:8]
        )
    else:
        lines.append("- None")
    return "\n".join(lines) + "\n"


def render_analyzer_report(run: dict[str, Any]) -> str:
    hybrid = (run.get("analysis") or {}).get("hybrid_context") or {}
    lines = [
        "# Analyzer Agent Report",
        "",
        f"Run ID: {run.get('run_id', '')}",
        f"Topic: {hybrid.get('topic', '') or 'Not specified'}",
        f"Business angle: {hybrid.get('business_angle', '') or 'Not specified'}",
        f"Target audience: {hybrid.get('target_audience', '') or 'Not specified'}",
        f"CTA: {hybrid.get('key_call_to_action', '') or 'Not specified'}",
        "",
        "Strategic themes:",
    ]
    lines.extend(f"- {item}" for item in (hybrid.get("strategic_themes") or ["None"]))
    lines.extend(["", "Differentiation guardrails:"])
    lines.extend(f"- {item}" for item in (hybrid.get("differentiation_guardrails") or ["None"]))
    return "\n".join(lines) + "\n"


def render_writer_report(run: dict[str, Any]) -> str:
    hybrid = (run.get("analysis") or {}).get("hybrid_context") or {}
    lines = [
        "# Writer Agent Report",
        "",
        f"Run ID: {run.get('run_id', '')}",
        f"Targets: {', '.join(run.get('platform_targets') or [])}",
        f"Audience: {hybrid.get('target_audience', '') or 'Not specified'}",
        "",
        "Required references:",
    ]
    lines.extend(f"- {item}" for item in (hybrid.get("reference_guardrails") or ["None"]))
    lines.extend(["", "Forbidden phrases:"])
    lines.extend(f"- {item}" for item in (hybrid.get("forbidden_phrases") or ["None"]))
    return "\n".join(lines) + "\n"


def render_editor_agent_report(run: dict[str, Any]) -> str:
    gate = run.get("quality_gate") or {}
    lines = [
        "# Editor Agent Report",
        "",
        f"Run ID: {run.get('run_id', '')}",
        f"Overall pass: {gate.get('overall_pass', False)}",
        f"Publish ready: {gate.get('publish_ready', False)}",
        f"Review score: {gate.get('score', 0)}",
        "",
        "Publish blockers:",
    ]
    lines.extend(f"- {item}" for item in (gate.get("publish_blockers") or ["None"]))
    lines.extend(["", "Remediation:"])
    lines.extend(f"- {item}" for item in (gate.get("remediation") or ["None"]))
    return "\n".join(lines) + "\n"


def render_publisher_report(run: dict[str, Any]) -> str:
    strategy = (run.get("analysis") or {}).get("publish_strategy") or {}
    platform_plans = strategy.get("platforms") or {}
    lines = [
        "# Publisher Agent Report",
        "",
        f"Run ID: {run.get('run_id', '')}",
        f"Approval state: {run.get('approval_state', '')}",
        f"Overall status: {run.get('status', '')}",
        f"Confidence: {run.get('confidence', 'n/a')}",
        "",
        "Platform states:",
    ]
    post_results = run.get("post_results") or {}
    for platform in run.get("platform_targets") or []:
        result = post_results.get(platform) or {}
        plan = platform_plans.get(platform) or {}
        browser_health = plan.get("browser_health") or {}
        lines.append(
            f"- {platform}: {result.get('status', 'pending')} | attempts={result.get('attempts', 0)} | failure={result.get('failure_reason', '') or 'none'} | url={result.get('url', '') or 'n/a'} | browser={browser_health.get('status', 'unknown')}"
        )
    return "\n".join(lines) + "\n"


def render_hybrid_context(run: dict[str, Any]) -> str:
    hybrid = (run.get("analysis") or {}).get("hybrid_context") or {}
    lines = [
        "# Hybrid Context",
        "",
        f"Topic: {hybrid.get('topic', '') or 'Not specified'}",
        f"Business angle: {hybrid.get('business_angle', '') or 'Not specified'}",
        f"Target audience: {hybrid.get('target_audience', '') or 'Not specified'}",
        f"Key CTA: {hybrid.get('key_call_to_action', '') or 'Not specified'}",
        "",
        "Strategic themes:",
    ]
    lines.extend(f"- {item}" for item in (hybrid.get("strategic_themes") or ["None"]))
    lines.extend(["", "Memory titles:"])
    lines.extend(f"- {item}" for item in (hybrid.get("memory_titles") or ["None"]))
    lines.extend(["", "Graph entities:"])
    lines.extend(f"- {item}" for item in (hybrid.get("graph_entities") or ["None"]))
    lines.extend(["", "Trend titles:"])
    lines.extend(f"- {item}" for item in (hybrid.get("trend_titles") or ["None"]))
    lines.extend(["", "Reference guardrails:"])
    lines.extend(f"- {item}" for item in (hybrid.get("reference_guardrails") or ["None"]))
    lines.extend(["", "Differentiation guardrails:"])
    lines.extend(f"- {item}" for item in (hybrid.get("differentiation_guardrails") or ["None"]))
    return "\n".join(lines) + "\n"


def _browser_health_payload(config: AppConfig, platform: str) -> dict[str, Any]:
    path = config.data_dir / "browser-health" / f"{platform}.json"
    if not path.exists():
        return {}
    payload = load_json(path, default={})
    if isinstance(payload, dict):
        return payload
    return {}


def _pipeline_step_payload(
    *,
    step: int,
    key: str,
    title: str,
    owner: str,
    status: str,
    detail: str,
    timestamp: str = "",
) -> dict[str, Any]:
    return {
        "step": step,
        "key": key,
        "title": title,
        "owner": owner,
        "status": status,
        "detail": detail,
        "timestamp": timestamp,
    }


def build_delivery_pipeline_payload(config: AppConfig, run: dict[str, Any]) -> dict[str, Any]:
    timestamps = run.get("stage_timestamps") or {}
    status = str(run.get("status") or "").strip().lower()
    approval_state = str(run.get("approval_state") or "pending").strip().lower()
    quality_gate = run.get("quality_gate") or {}
    publish_ready = bool(quality_gate.get("publish_ready", False))
    platform_targets = list(run.get("platform_targets") or [])
    post_results = run.get("post_results") or {}
    posted_platforms = []
    failed_platforms = []
    touched_platforms = []
    for platform in platform_targets:
        platform_status = (
            str((post_results.get(platform) or {}).get("status") or "").strip().lower()
        )
        if platform_status == "posted":
            posted_platforms.append(platform)
        elif platform_status == "failed":
            failed_platforms.append(platform)
        elif platform_status not in {"", "pending"}:
            touched_platforms.append(platform)

    preview_sent = bool(run.get("telegram_preview_sent_at"))
    telegram_configured = bool(config.telegram_target and config.telegram_bot_token)

    step_1_status = (
        "done"
        if (timestamps.get("ingested") or (run.get("article") or {}).get("clean_text"))
        else "pending"
    )
    step_2_status = (
        "done" if (timestamps.get("researched") or run.get("trend_signals")) else "pending"
    )
    if timestamps.get("reviewed"):
        step_3_status = "done"
    elif timestamps.get("drafted") or timestamps.get("packaged"):
        step_3_status = "in_progress"
    else:
        step_3_status = "pending"
    step_4_status = "done" if timestamps.get("image_generated") else "pending"

    if approval_state == "approved":
        step_5_status = "done"
        step_5_detail = (
            "Telegram approval is complete and OpenClaw can continue with automated posting."
        )
    elif approval_state == "rejected":
        step_5_status = "failed"
        step_5_detail = (
            "The operator rejected the run in Telegram. Fix the drafts or promote a new brief."
        )
    elif not telegram_configured:
        step_5_status = "blocked"
        step_5_detail = "Telegram is not configured in the workspace, so operator approval and overrides are unavailable."
    elif preview_sent:
        step_5_status = "waiting"
        step_5_detail = "Telegram preview was sent. Waiting for /approve or /reject."
    elif timestamps.get("reviewed") or timestamps.get("image_generated"):
        step_5_status = "in_progress"
        step_5_detail = "OpenClaw finished the draft package and is surfacing the run in Telegram for approval and monitoring."
    else:
        step_5_status = "pending"
        step_5_detail = (
            "Telegram control becomes active once the draft package and image are ready."
        )

    if failed_platforms:
        step_6_status = "failed"
        step_6_detail = f"Publishing failed on: {', '.join(failed_platforms)}."
    elif platform_targets and len(posted_platforms) == len(platform_targets):
        step_6_status = "done"
        step_6_detail = "All configured platforms were posted successfully."
    elif approval_state != "approved":
        step_6_status = "waiting"
        step_6_detail = (
            "OpenClaw browser waits for Telegram approval before starting multi-platform posting."
        )
    elif touched_platforms or status == "posting":
        step_6_status = "in_progress"
        step_6_detail = "OpenClaw browser is posting or staging the approved drafts."
    elif publish_ready:
        step_6_status = "ready"
        step_6_detail = "Approval is complete. OpenClaw browser can post automatically to LinkedIn, Facebook, and X."
    else:
        step_6_status = "blocked"
        step_6_detail = "Publishing is blocked until the review gate and confidence gate pass."

    steps = [
        _pipeline_step_payload(
            step=1,
            key="ingest",
            title="Ingest daily brief + competitor article",
            owner="OpenClaw scheduled workflow",
            status=step_1_status,
            detail="Load the active daily brief and scrape the competitor article safely.",
            timestamp=str(timestamps.get("ingested") or ""),
        ),
        _pipeline_step_payload(
            step=2,
            key="research",
            title="Run zero-cost trending research",
            owner="OpenClaw research skills",
            status=step_2_status,
            detail="Collect RSS, public news feeds, pytrends, and SearXNG signals around the topic.",
            timestamp=str(timestamps.get("researched") or ""),
        ),
        _pipeline_step_payload(
            step=3,
            key="analyze_and_generate",
            title="Analyze + generate article and social drafts",
            owner="OpenClaw + LangGraph agents",
            status=step_3_status,
            detail="Synthesize competitor, trend, memory, and graph context into original article and social drafts.",
            timestamp=str(timestamps.get("reviewed") or timestamps.get("drafted") or ""),
        ),
        _pipeline_step_payload(
            step=4,
            key="generate_image",
            title="Generate the image asset",
            owner="OpenClaw image backend",
            status=step_4_status,
            detail="Build the social image used by the publication flow.",
            timestamp=str(timestamps.get("image_generated") or ""),
        ),
        _pipeline_step_payload(
            step=5,
            key="telegram_control",
            title="Telegram monitoring, approval, and overrides",
            owner="Telegram bot + OpenClaw plugin",
            status=step_5_status,
            detail=step_5_detail,
            timestamp=str(
                timestamps.get("preview_sent")
                or timestamps.get("approved")
                or timestamps.get("rejected")
                or ""
            ),
        ),
        _pipeline_step_payload(
            step=6,
            key="auto_publish",
            title="Automatically post to LinkedIn, Facebook, and X",
            owner="OpenClaw browser automation",
            status=step_6_status,
            detail=step_6_detail,
            timestamp=str(
                timestamps.get("posted_linkedin")
                or timestamps.get("posted_facebook")
                or timestamps.get("posted_x")
                or timestamps.get("publish_prepared_linkedin")
                or timestamps.get("approved")
                or ""
            ),
        ),
    ]

    next_action = "Workflow complete."
    current_step = 6
    if step_1_status != "done":
        current_step = 1
        next_action = "Let OpenClaw ingest the active daily brief and competitor URL."
    elif step_2_status != "done":
        current_step = 2
        next_action = "Let OpenClaw finish the zero-cost trend research stage."
    elif step_3_status != "done":
        current_step = 3
        next_action = "Let OpenClaw finish the analysis and draft generation stage."
    elif step_4_status != "done":
        current_step = 4
        next_action = "Generate the image asset before requesting approval."
    elif step_5_status == "blocked":
        current_step = 5
        next_action = (
            "Configure Telegram so the operator can approve, monitor, and override the run."
        )
    elif step_5_status == "in_progress":
        current_step = 5
        next_action = "OpenClaw should send the Telegram preview and expose the operator controls."
    elif approval_state == "pending":
        current_step = 5
        next_action = f"Approve the run in Telegram with /approve {run.get('run_id', '')}."
    elif step_6_status in {"ready", "in_progress"}:
        current_step = 6
        next_action = (
            "OpenClaw browser should publish the approved drafts across all configured platforms."
        )
    elif step_6_status == "failed":
        current_step = 6
        next_action = (
            "Retry the failed platform publish steps after fixing browser or platform issues."
        )

    return {
        "trigger": "OpenClaw cron/heartbeat",
        "approval_required": bool(config.approval_required),
        "telegram_configured": telegram_configured,
        "preview_sent": preview_sent,
        "current_step": current_step,
        "next_action": next_action,
        "steps": steps,
        "platforms_posted": posted_platforms,
        "platforms_failed": failed_platforms,
        "platforms_pending": [
            platform
            for platform in platform_targets
            if platform not in posted_platforms and platform not in failed_platforms
        ],
        "openclaw_role": "OpenClaw cron/heartbeat triggers the run, executes steps 1-4, then completes step 6 after Telegram approval.",
        "telegram_role": "Telegram is the full operator chat interface for status, previews, approval, logs, manual triggers, and overrides.",
    }


def render_delivery_pipeline(run: dict[str, Any]) -> str:
    pipeline = (run.get("analysis") or {}).get("delivery_pipeline") or {}
    lines = [
        "# Delivery Pipeline",
        "",
        f"Trigger: {pipeline.get('trigger', 'OpenClaw cron/heartbeat')}",
        f"Current step: {pipeline.get('current_step', 'unknown')}/6",
        f"Next action: {pipeline.get('next_action', 'n/a')}",
        "",
        f"OpenClaw role: {pipeline.get('openclaw_role', '')}",
        f"Telegram role: {pipeline.get('telegram_role', '')}",
        "",
    ]
    for item in pipeline.get("steps") or []:
        lines.extend(
            [
                f"## Step {item.get('step', '?')}: {item.get('title', '')}",
                f"- Status: {item.get('status', 'unknown')}",
                f"- Owner: {item.get('owner', '')}",
                f"- Detail: {item.get('detail', '')}",
                f"- Timestamp: {item.get('timestamp', '') or 'n/a'}",
                "",
            ]
        )
    return "\n".join(lines).rstrip() + "\n"


def _competitor_domain(run: dict[str, Any]) -> str:
    article = run.get("article") or {}
    url = str(article.get("url") or run.get("competitor_url") or "").strip()
    if not url:
        return ""
    try:
        from urllib.parse import urlparse

        return urlparse(url).netloc.lower().replace("www.", "")
    except Exception:
        return ""


def _platform_disclosure_text(run: dict[str, Any], platform: str) -> str:
    domain = _competitor_domain(run) or "the competitor source"
    if platform == "x":
        return (
            f"Source-informed response to public reporting from {domain}. Reviewed before posting."
        )
    return f"Source-informed response to public reporting from {domain}, drafted in OpenClaw and reviewed before posting."


def build_evidence_pack_payload(run: dict[str, Any]) -> dict[str, Any]:
    article = run.get("article") or {}
    quality_gate = run.get("quality_gate") or {}
    metrics = quality_gate.get("metrics") or {}
    trend_signals = run.get("trend_signals") or []
    trend_sources = _dedupe_strings(
        [str(item.get("source") or "").strip() for item in trend_signals], limit=8
    )
    trend_evidence = [
        {
            "title": str(item.get("title") or "").strip(),
            "source": str(item.get("source") or "").strip(),
            "url": str(item.get("url") or "").strip(),
            "score": item.get("score", 0),
        }
        for item in trend_signals[:8]
        if str(item.get("title") or "").strip()
    ]
    mandatory_references = list((run.get("daily_input") or {}).get("mandatory_references") or [])
    identity = run.get("identity") or {}
    source_registry = (run.get("analysis") or {}).get("source_registry") or {}
    return {
        "competitor_title": str(article.get("title") or "").strip(),
        "competitor_domain": _competitor_domain(run),
        "competitor_url": str(article.get("url") or run.get("competitor_url") or "").strip(),
        "canonical_url": str(
            article.get("canonical_url") or article.get("url") or run.get("competitor_url") or ""
        ).strip(),
        "canonical_url_hash": str(identity.get("canonical_url_hash") or ""),
        "content_hash": str(identity.get("content_hash") or ""),
        "source_safety_severity": metrics.get("source_safety_severity", "unknown"),
        "source_safety_flags": list(metrics.get("source_safety_flags") or []),
        "trend_source_count": len(trend_sources),
        "trend_sources": trend_sources,
        "trend_evidence": trend_evidence,
        "memory_context_titles": _dedupe_strings(
            [humanize_memory_hit(item) for item in (run.get("memory_hits") or [])], limit=5
        ),
        "graph_context_entities": _dedupe_strings(
            [
                str(item.get("label") or item.get("id") or "").strip()
                for item in (run.get("graph_hits") or [])
            ],
            limit=6,
        ),
        "mandatory_references": mandatory_references,
        "mandatory_references_present": bool(
            (quality_gate.get("checks") or {}).get(
                "mandatory_references_present", not mandatory_references
            )
        ),
        "attribution_ready": bool(
            (quality_gate.get("checks") or {}).get("article_attributed", False)
        ),
        "trend_context_ready": bool(
            (quality_gate.get("checks") or {}).get("trend_context_present", False)
        ),
        "source_registry_ids": [
            str(cast(dict[str, Any], item).get("source_id") or "")
            for item in (
                [source_registry.get("competitor")]
                if isinstance(source_registry.get("competitor"), dict)
                else []
            )
            + list(source_registry.get("trend_sources") or [])
            if isinstance(item, dict) and str(item.get("source_id") or "").strip()
        ],
    }


def render_evidence_pack(run: dict[str, Any]) -> str:
    evidence = (run.get("analysis") or {}).get("evidence_pack") or {}
    lines = [
        "# Evidence Pack",
        "",
        f"Competitor title: {evidence.get('competitor_title', '') or 'Unknown'}",
        f"Competitor domain: {evidence.get('competitor_domain', '') or 'Unknown'}",
        f"Competitor URL: {evidence.get('competitor_url', '') or 'Unknown'}",
        f"Canonical URL: {evidence.get('canonical_url', '') or 'Unknown'}",
        f"Canonical URL hash: {evidence.get('canonical_url_hash', '') or 'Unknown'}",
        f"Content hash: {evidence.get('content_hash', '') or 'Unknown'}",
        f"Source safety severity: {evidence.get('source_safety_severity', 'unknown')}",
        f"Trend source count: {evidence.get('trend_source_count', 0)}",
        f"Attribution ready: {evidence.get('attribution_ready', False)}",
        f"Mandatory references present: {evidence.get('mandatory_references_present', False)}",
        f"Trend context ready: {evidence.get('trend_context_ready', False)}",
        "",
        "Trend sources:",
    ]
    lines.extend(f"- {item}" for item in (evidence.get("trend_sources") or ["None"]))
    lines.extend(["", "Top trend evidence:"])
    if evidence.get("trend_evidence"):
        lines.extend(
            f"- {item.get('title', '')} | source={item.get('source', '')} | score={item.get('score', 0)} | url={item.get('url', '')}"
            for item in (evidence.get("trend_evidence") or [])
        )
    else:
        lines.append("- None")
    lines.extend(["", "Historical memory cues:"])
    lines.extend(f"- {item}" for item in (evidence.get("memory_context_titles") or ["None"]))
    lines.extend(["", "Knowledge graph cues:"])
    lines.extend(f"- {item}" for item in (evidence.get("graph_context_entities") or ["None"]))
    lines.extend(["", "Mandatory references:"])
    lines.extend(f"- {item}" for item in (evidence.get("mandatory_references") or ["None"]))
    lines.extend(["", "Source registry entries:"])
    lines.extend(f"- {item}" for item in (evidence.get("source_registry_ids") or ["None"]))
    return "\n".join(lines) + "\n"


def build_publish_strategy_payload(config: AppConfig, run: dict[str, Any]) -> dict[str, Any]:
    outputs = run.get("outputs") or {}
    post_results = run.get("post_results") or {}
    platforms = {}
    ready_count = 0
    known_health = 0
    for platform in run.get("platform_targets") or []:
        browser_health = _browser_health_payload(config, platform)
        if browser_health:
            known_health += 1
            if browser_health.get("status") == "ready":
                ready_count += 1
        platforms[platform] = {
            "browser_profile": browser_profile_for(config, platform),
            "compose_url": compose_url_for(config, platform),
            "draft_path": outputs.get(platform, f"drafts/{platform}.md"),
            "image_path": outputs.get("image_asset", "media/social_card.png"),
            "disclosure_text": _platform_disclosure_text(run, platform),
            "attribution_hint": f"Reference {_competitor_domain(run) or 'the competitor source'} when needed.",
            "call_to_action": str(
                ((run.get("analysis") or {}).get("hybrid_context") or {}).get("key_call_to_action")
                or ""
            ).strip(),
            "trend_titles": list(
                (
                    ((run.get("analysis") or {}).get("hybrid_context") or {}).get("trend_titles")
                    or []
                )[:4]
            ),
            "browser_health": browser_health,
            "post_status": (post_results.get(platform) or {}).get("status", "pending"),
        }
    return {
        "all_known_profiles_ready": bool(platforms)
        and ready_count == len(platforms)
        and known_health == len(platforms),
        "ready_profiles": ready_count,
        "known_health_profiles": known_health,
        "platforms": platforms,
    }


def render_publish_strategy(run: dict[str, Any]) -> str:
    strategy = (run.get("analysis") or {}).get("publish_strategy") or {}
    lines = [
        "# Publish Strategy",
        "",
        f"Known ready profiles: {strategy.get('ready_profiles', 0)}/{len(strategy.get('platforms') or {})}",
        f"All known profiles ready: {strategy.get('all_known_profiles_ready', False)}",
    ]
    for platform, payload in (strategy.get("platforms") or {}).items():
        browser = payload.get("browser_health") or {}
        lines.extend(
            [
                "",
                f"## {platform}",
                f"- Browser profile: {payload.get('browser_profile', '')}",
                f"- Compose URL: {payload.get('compose_url', '')}",
                f"- Draft path: {payload.get('draft_path', '')}",
                f"- Image path: {payload.get('image_path', '')}",
                f"- Disclosure text: {payload.get('disclosure_text', '')}",
                f"- Attribution hint: {payload.get('attribution_hint', '')}",
                f"- CTA: {payload.get('call_to_action', '') or 'None'}",
                f"- Browser health: {browser.get('status', 'unknown')} ({browser.get('reason', '') or 'ok'})",
                f"- Current post status: {payload.get('post_status', 'pending')}",
            ]
        )
        trend_titles = payload.get("trend_titles") or []
        lines.append("- Trend anchors: " + (", ".join(trend_titles) if trend_titles else "None"))
    return "\n".join(lines) + "\n"


def build_recovery_plan_payload(config: AppConfig, run: dict[str, Any]) -> dict[str, Any]:
    quality_gate = run.get("quality_gate") or {}
    remediation = list(quality_gate.get("remediation") or [])
    blockers = list(quality_gate.get("publish_blockers") or [])
    strategy = build_publish_strategy_payload(config, run)
    items = []

    if not prompt_is_actionable(run.get("prompt", "")):
        items.append(
            {
                "severity": "high",
                "category": "configuration",
                "action": "Replace the placeholder daily prompt or promote a ready queued brief with a concrete topic, audience, angle, and CTA.",
            }
        )
    if run.get("approval_state") == "pending":
        items.append(
            {
                "severity": "medium",
                "category": "approval",
                "action": f"Review and approve or reject {run.get('run_id', '')}.",
            }
        )
    for blocker, action in zip(blockers, remediation, strict=False):
        items.append(
            {"severity": "high", "category": "quality_gate", "action": f"{blocker}: {action}"}
        )
    for platform, payload in (strategy.get("platforms") or {}).items():
        browser = payload.get("browser_health") or {}
        if browser and browser.get("status") != "ready":
            severity = (
                "high" if browser.get("reason") in {"not_logged_in", "challenge_page"} else "medium"
            )
            items.append(
                {
                    "severity": severity,
                    "category": "browser_profile",
                    "action": f"Refresh the {platform} browser profile: {browser.get('reason') or browser.get('status')}.",
                }
            )
    for platform, result in (run.get("post_results") or {}).items():
        if result.get("status") == "failed":
            items.append(
                {
                    "severity": "high",
                    "category": "publishing",
                    "action": f"Retry {platform} after fixing failure `{result.get('failure_reason') or 'unknown'}`.",
                }
            )
    return {
        "status": "ok" if not items else "attention",
        "items": items,
        "counts": {
            "total": len(items),
            "high": sum(1 for item in items if item["severity"] == "high"),
            "medium": sum(1 for item in items if item["severity"] == "medium"),
            "low": sum(1 for item in items if item["severity"] == "low"),
        },
    }


def render_recovery_plan(run: dict[str, Any]) -> str:
    plan = (run.get("analysis") or {}).get("recovery_plan") or {}
    lines = [
        "# Recovery Plan",
        "",
        f"Status: {plan.get('status', 'unknown')}",
        f"Total items: {(plan.get('counts') or {}).get('total', 0)}",
        "",
    ]
    items = plan.get("items") or []
    if not items:
        lines.append("- No immediate recovery action.")
    else:
        for item in items:
            lines.append(
                f"- [{item.get('severity', 'info')}] {item.get('category', 'general')}: {item.get('action', '')}"
            )
    return "\n".join(lines) + "\n"


def _timezone(config: AppConfig) -> ZoneInfo:
    try:
        return ZoneInfo(config.operator_timezone or "Africa/Tunis")
    except Exception:
        return ZoneInfo("Africa/Tunis")


def _platform_windows(platform: str) -> list[tuple[str, str]]:
    defaults = {
        "linkedin": [("08:30", "10:30"), ("13:00", "15:00")],
        "facebook": [("11:30", "13:30"), ("18:00", "20:00")],
        "x": [("09:00", "11:00"), ("17:00", "19:00")],
    }
    return defaults.get(platform, [("09:00", "11:00")])


def _next_window_payload(base_time: datetime, windows: list[tuple[str, str]]) -> dict[str, Any]:
    for day_offset in range(0, 3):
        current_day = (base_time + timedelta(days=day_offset)).date()
        for start_text, end_text in windows:
            start_hour, start_minute = [int(part) for part in start_text.split(":", 1)]
            end_hour, end_minute = [int(part) for part in end_text.split(":", 1)]
            start_dt = datetime(
                current_day.year,
                current_day.month,
                current_day.day,
                start_hour,
                start_minute,
                tzinfo=base_time.tzinfo,
            )
            end_dt = datetime(
                current_day.year,
                current_day.month,
                current_day.day,
                end_hour,
                end_minute,
                tzinfo=base_time.tzinfo,
            )
            if start_dt <= base_time <= end_dt:
                return {
                    "status": "open_now",
                    "window": f"{start_text}-{end_text}",
                    "next_start_local": start_dt.isoformat(),
                    "next_end_local": end_dt.isoformat(),
                }
            if base_time < start_dt:
                return {
                    "status": "upcoming",
                    "window": f"{start_text}-{end_text}",
                    "next_start_local": start_dt.isoformat(),
                    "next_end_local": end_dt.isoformat(),
                }
    return {
        "status": "unknown",
        "window": "",
        "next_start_local": "",
        "next_end_local": "",
    }


def build_publication_timing_payload(config: AppConfig, run: dict[str, Any]) -> dict[str, Any]:
    tz = _timezone(config)
    base_time = (
        _parse_iso(run.get("updated_at", ""))
        or _parse_iso(run.get("created_at", ""))
        or datetime.now(UTC)
    ).astimezone(tz)
    payload: dict[str, Any] = {
        "timezone": getattr(tz, "key", str(tz)),
        "reference_time_local": base_time.isoformat(),
        "platforms": {},
    }
    for platform in run.get("platform_targets") or []:
        windows = _platform_windows(platform)
        next_window = _next_window_payload(base_time, windows)
        payload["platforms"][platform] = {
            "recommended_windows": [f"{start}-{end}" for start, end in windows],
            **next_window,
        }
    return payload


def render_publication_timing(run: dict[str, Any]) -> str:
    timing = (run.get("analysis") or {}).get("publication_timing") or {}
    lines = [
        "# Publication Timing",
        "",
        f"Timezone: {timing.get('timezone', 'unknown')}",
        f"Reference time: {timing.get('reference_time_local', '') or 'unknown'}",
    ]
    for platform, payload in (timing.get("platforms") or {}).items():
        lines.extend(
            [
                "",
                f"## {platform}",
                f"- Recommended windows: {', '.join(payload.get('recommended_windows') or []) or 'None'}",
                f"- Current state: {payload.get('status', 'unknown')}",
                f"- Next window: {payload.get('window', '') or 'unknown'}",
                f"- Next start: {payload.get('next_start_local', '') or 'unknown'}",
                f"- Next end: {payload.get('next_end_local', '') or 'unknown'}",
            ]
        )
    return "\n".join(lines) + "\n"


def build_provenance_manifest_payload(config: AppConfig, run: dict[str, Any]) -> dict[str, Any]:
    analysis = run.get("analysis") or {}
    outputs = run.get("outputs") or {}
    trend_signals = run.get("trend_signals") or []
    identity = run.get("identity") or {}
    source_registry = analysis.get("source_registry") or {}
    return {
        "run_id": run.get("run_id", ""),
        "created_at": run.get("created_at", ""),
        "updated_at": run.get("updated_at", ""),
        "trigger_kind": run.get("trigger_kind", "manual"),
        "stage_timestamps": dict(run.get("stage_timestamps") or {}),
        "graph_mode": run.get("graph_mode", "sequential"),
        "model_surface": (run.get("cost_proof") or {}).get(
            "llm_surface", config.preferred_model or "set-in-openclaw"
        ),
        "free_backends_used": list((run.get("cost_proof") or {}).get("free_backends_used") or []),
        "image_backend": (run.get("cost_proof") or {}).get("image_backend", ""),
        "prompt_source": str((run.get("daily_input") or {}).get("prompt_source") or "direct"),
        "versions": {
            "schema_version": str(run.get("schema_version") or config.schema_version),
            "publish_adapter_version": str(
                run.get("publish_adapter_version") or config.publish_adapter_version
            ),
            "prompt_bundle_version": str(
                run.get("prompt_bundle_version") or config.prompt_bundle_version
            ),
            "retrieval_policy_version": str(
                run.get("retrieval_policy_version") or config.retrieval_policy_version
            ),
            "scoring_policy_version": str(
                run.get("scoring_policy_version") or config.scoring_policy_version
            ),
            "approval_policy": str(run.get("approval_policy") or config.approval_policy),
        },
        "identity": dict(identity),
        "competitor_source": {
            "title": str((run.get("article") or {}).get("title") or "").strip(),
            "url": str(
                (run.get("article") or {}).get("url") or run.get("competitor_url") or ""
            ).strip(),
            "canonical_url": str(
                (run.get("article") or {}).get("canonical_url")
                or (run.get("article") or {}).get("url")
                or run.get("competitor_url")
                or ""
            ).strip(),
            "domain": _competitor_domain(run),
        },
        "trend_sources": [
            {
                "title": str(item.get("title") or "").strip(),
                "source": str(item.get("source") or "").strip(),
                "url": str(item.get("url") or "").strip(),
            }
            for item in trend_signals[:10]
            if str(item.get("title") or "").strip()
        ],
        "memory_titles": _dedupe_strings(
            [humanize_memory_hit(item) for item in (run.get("memory_hits") or [])], limit=8
        ),
        "graph_entities": _dedupe_strings(
            [
                str(item.get("label") or item.get("id") or "").strip()
                for item in (run.get("graph_hits") or [])
            ],
            limit=10,
        ),
        "source_registry": dict(source_registry),
        "quality_gate": {
            "score": (run.get("quality_gate") or {}).get("score", 0),
            "publish_ready": (run.get("quality_gate") or {}).get("publish_ready", False),
            "issues": list((run.get("quality_gate") or {}).get("issues") or []),
            "publish_blockers": list((run.get("quality_gate") or {}).get("publish_blockers") or []),
        },
        "outputs": dict(outputs),
        "post_results": dict(run.get("post_results") or {}),
        "proof_pack": dict(run.get("proof_pack") or {}),
    }


def render_provenance_manifest(run: dict[str, Any]) -> str:
    manifest = (run.get("analysis") or {}).get("provenance_manifest") or {}
    lines = [
        "# Provenance Manifest",
        "",
        f"Run ID: {manifest.get('run_id', '')}",
        f"Created at: {manifest.get('created_at', '')}",
        f"Updated at: {manifest.get('updated_at', '')}",
        f"Trigger kind: {manifest.get('trigger_kind', '')}",
        f"Graph mode: {manifest.get('graph_mode', '')}",
        f"Model surface: {manifest.get('model_surface', '')}",
        f"Free backends: {', '.join(manifest.get('free_backends_used') or []) or 'None'}",
        f"Image backend: {manifest.get('image_backend', '') or 'unknown'}",
        f"Prompt source: {manifest.get('prompt_source', '') or 'unknown'}",
        "",
        "Versions:",
        f"- Schema: {(manifest.get('versions') or {}).get('schema_version', '')}",
        f"- Publish adapter: {(manifest.get('versions') or {}).get('publish_adapter_version', '')}",
        f"- Prompt bundle: {(manifest.get('versions') or {}).get('prompt_bundle_version', '')}",
        f"- Retrieval policy: {(manifest.get('versions') or {}).get('retrieval_policy_version', '')}",
        f"- Scoring policy: {(manifest.get('versions') or {}).get('scoring_policy_version', '')}",
        f"- Approval policy: {(manifest.get('versions') or {}).get('approval_policy', '')}",
        "",
        "Identity:",
        f"- Input hash: {(manifest.get('identity') or {}).get('input_hash', '') or 'unknown'}",
        f"- Prompt hash: {(manifest.get('identity') or {}).get('prompt_hash', '') or 'unknown'}",
        f"- Competitor URL hash: {(manifest.get('identity') or {}).get('competitor_url_hash', '') or 'unknown'}",
        f"- Canonical URL hash: {(manifest.get('identity') or {}).get('canonical_url_hash', '') or 'unknown'}",
        f"- Content hash: {(manifest.get('identity') or {}).get('content_hash', '') or 'unknown'}",
        "",
        "Competitor source:",
        f"- Title: {(manifest.get('competitor_source') or {}).get('title', '') or 'Unknown'}",
        f"- Domain: {(manifest.get('competitor_source') or {}).get('domain', '') or 'Unknown'}",
        f"- URL: {(manifest.get('competitor_source') or {}).get('url', '') or 'Unknown'}",
        f"- Canonical URL: {(manifest.get('competitor_source') or {}).get('canonical_url', '') or 'Unknown'}",
        "",
        "Trend evidence:",
    ]
    trend_sources = manifest.get("trend_sources") or []
    if trend_sources:
        lines.extend(
            f"- {item.get('title', '')} | {item.get('source', '')} | {item.get('url', '')}"
            for item in trend_sources
        )
    else:
        lines.append("- None")
    lines.extend(["", "Stage timestamps:"])
    for key, value in (manifest.get("stage_timestamps") or {}).items():
        lines.append(f"- {key}: {value}")
    lines.extend(["", "Quality gate:"])
    quality = manifest.get("quality_gate") or {}
    lines.append(f"- Score: {quality.get('score', 0)}")
    lines.append(f"- Publish ready: {quality.get('publish_ready', False)}")
    lines.append("- Issues: " + (", ".join(quality.get("issues") or []) or "None"))
    lines.append("- Blockers: " + (", ".join(quality.get("publish_blockers") or []) or "None"))
    lines.extend(["", "Source registry:"])
    registry = manifest.get("source_registry") or {}
    competitor_registry = registry.get("competitor") or {}
    lines.append(
        f"- Competitor adapter: {competitor_registry.get('source_id', 'unknown')} | acquisition={competitor_registry.get('acquisition', 'unknown')}"
    )
    for item in registry.get("trend_sources") or []:
        lines.append(
            f"- Trend adapter: {item.get('source_id', 'unknown')} | acquisition={item.get('acquisition', 'unknown')}"
        )
    return "\n".join(lines) + "\n"


def render_source_registry(run: dict[str, Any]) -> str:
    registry = (run.get("analysis") or {}).get("source_registry") or {}
    lines = [
        "# Source Registry",
        "",
        f"Registry path: {registry.get('registry_path', '') or 'unknown'}",
        "",
    ]
    competitor = registry.get("competitor") or {}
    if competitor:
        lines.extend(
            [
                "## Competitor adapter",
                f"- Source ID: {competitor.get('source_id', 'unknown')}",
                f"- Type: {competitor.get('type', 'unknown')}",
                f"- Acquisition: {competitor.get('acquisition', 'unknown')}",
                f"- Legal notes: {competitor.get('legal_notes', '') or 'n/a'}",
                "",
            ]
        )
    trend_sources = registry.get("trend_sources") or []
    lines.append("## Trend adapters")
    if trend_sources:
        for item in trend_sources:
            lines.append(
                f"- {item.get('source_id', 'unknown')} | type={item.get('type', 'unknown')} | acquisition={item.get('acquisition', 'unknown')}"
            )
    else:
        lines.append("- None")
    return "\n".join(lines) + "\n"


def render_proof_pack(run: dict[str, Any]) -> str:
    proof_pack = run.get("proof_pack") or {}
    lines = [
        "# Proof Pack",
        "",
        f"Run ID: {proof_pack.get('run_id', run.get('run_id', ''))}",
        f"Retention days: {proof_pack.get('retention_days_proofs', 'unknown')}",
        "",
    ]
    results = proof_pack.get("results") or {}
    if not results:
        lines.append("- No platform proof entries yet.")
        return "\n".join(lines) + "\n"
    for platform, item in results.items():
        lines.extend(
            [
                f"## {platform}",
                f"- Proof ID: {item.get('proof_id', 'unknown')}",
                f"- Status: {item.get('status', 'pending')}",
                f"- Final URL: {item.get('final_url', '') or 'n/a'}",
                f"- Publish target hash: {item.get('publish_target_hash', '') or 'n/a'}",
                f"- Canonical post hash: {item.get('canonical_post_hash', '') or 'n/a'}",
                f"- Idempotency key: {item.get('idempotency_key', '') or 'n/a'}",
                f"- Published text hash: {item.get('published_text_hash', '') or 'n/a'}",
                f"- Image asset hash: {item.get('image_asset_hash', '') or 'n/a'}",
                f"- Adapter version: {item.get('adapter_version', '') or 'n/a'}",
                f"- Failure reason: {item.get('failure_reason', '') or 'none'}",
                "- Screenshot hashes:",
            ]
        )
        screenshot_hashes = item.get("screenshot_hashes") or []
        if screenshot_hashes:
            for screenshot in screenshot_hashes:
                lines.append(
                    f"  - {screenshot.get('path', '')}: {screenshot.get('sha256', '') or 'missing'}"
                )
        else:
            lines.append("  - none")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def build_approval_packet_payload(config: AppConfig, run: dict[str, Any]) -> dict[str, Any]:
    quality_gate = run.get("quality_gate") or {}
    publish_strategy = build_publish_strategy_payload(config, run)
    timing = build_publication_timing_payload(config, run)
    confidence = (
        _confidence_value(run) if (run.get("stage_timestamps") or {}).get("reviewed") else 0.0
    )
    blockers = list(quality_gate.get("publish_blockers") or [])
    browser_blockers = [
        platform
        for platform, payload in (publish_strategy.get("platforms") or {}).items()
        if (payload.get("browser_health") or {}).get("status") not in {"", "ready"}
    ]
    if blockers or browser_blockers or confidence < config.confidence_threshold:
        recommendation = "revise_or_hold"
    elif run.get("approval_state") == "approved":
        recommendation = "approved_ready_to_schedule"
    else:
        recommendation = "approve_when_ready"
    if blockers:
        risk_level = "high"
    elif browser_blockers or confidence < (config.confidence_threshold + 0.05):
        risk_level = "medium"
    else:
        risk_level = "low"
    return {
        "recommendation": recommendation,
        "risk_level": risk_level,
        "confidence": confidence,
        "publish_ready": quality_gate.get("publish_ready", False),
        "approval_state": run.get("approval_state", "pending"),
        "blockers": blockers,
        "browser_blockers": browser_blockers,
        "top_issues": list((quality_gate.get("issues") or [])[:6]),
        "remediation": list((quality_gate.get("remediation") or [])[:6]),
        "next_windows": {
            platform: {
                "status": payload.get("status", ""),
                "window": payload.get("window", ""),
                "next_start_local": payload.get("next_start_local", ""),
            }
            for platform, payload in (timing.get("platforms") or {}).items()
        },
    }


def render_approval_packet(run: dict[str, Any]) -> str:
    packet = (run.get("analysis") or {}).get("approval_packet") or {}
    lines = [
        "# Approval Packet",
        "",
        f"Recommendation: {packet.get('recommendation', 'unknown')}",
        f"Risk level: {packet.get('risk_level', 'unknown')}",
        f"Confidence: {packet.get('confidence', 'n/a')}",
        f"Publish ready: {packet.get('publish_ready', False)}",
        f"Approval state: {packet.get('approval_state', '')}",
        "",
        "Blockers:",
    ]
    lines.extend(f"- {item}" for item in (packet.get("blockers") or ["None"]))
    lines.extend(["", "Browser blockers:"])
    lines.extend(f"- {item}" for item in (packet.get("browser_blockers") or ["None"]))
    lines.extend(["", "Top issues:"])
    lines.extend(f"- {item}" for item in (packet.get("top_issues") or ["None"]))
    lines.extend(["", "Remediation:"])
    lines.extend(f"- {item}" for item in (packet.get("remediation") or ["None"]))
    lines.extend(["", "Next publish windows:"])
    next_windows = packet.get("next_windows") or {}
    if next_windows:
        for platform, payload in next_windows.items():
            lines.append(
                f"- {platform}: {payload.get('status', 'unknown')} | {payload.get('window', '') or 'unknown'} | next_start={payload.get('next_start_local', '') or 'unknown'}"
            )
    else:
        lines.append("- None")
    return "\n".join(lines) + "\n"


def build_execution_policy_payload(config: AppConfig, run: dict[str, Any]) -> dict[str, Any]:
    quality_gate = run.get("quality_gate") or {}
    strategy = build_publish_strategy_payload(config, run)
    timing = build_publication_timing_payload(config, run)
    prompt_ok = prompt_is_actionable(run.get("prompt", ""))
    approval_state = str(run.get("approval_state") or "pending")
    publish_ready = bool(quality_gate.get("publish_ready", False))
    confidence = float(run.get("confidence", 0) or 0.0)
    policy = {
        "approval_state": approval_state,
        "publish_ready": publish_ready,
        "confidence": confidence,
        "threshold": config.confidence_threshold,
        "auto_publish_enabled": bool(config.auto_publish_enabled),
        "prompt_actionable": prompt_ok,
        "decision": "manual_intervention_required",
        "reason": "",
        "platforms": {},
    }

    blocking_reasons = []
    if not prompt_ok:
        blocking_reasons.append("daily_prompt_not_actionable")
    if approval_state != "approved":
        blocking_reasons.append("approval_missing")
    if not publish_ready:
        blocking_reasons.append("quality_gate_not_ready")
    if confidence < config.confidence_threshold:
        blocking_reasons.append("confidence_below_threshold")

    ready_now_count = 0
    wait_window_count = 0
    manual_count = 0
    for platform in run.get("platform_targets") or []:
        browser = ((strategy.get("platforms") or {}).get(platform) or {}).get(
            "browser_health"
        ) or {}
        timing_payload = (timing.get("platforms") or {}).get(platform) or {}
        post_result = (run.get("post_results") or {}).get(platform) or {}
        platform_blockers = list(blocking_reasons)
        if browser and browser.get("status") not in {"", "ready"}:
            platform_blockers.append(f"browser_{browser.get('reason') or browser.get('status')}")
        timing_status = timing_payload.get("status", "unknown")
        can_publish_now = not platform_blockers and timing_status in {"open_now", "unknown"}
        should_wait_for_window = not platform_blockers and timing_status == "upcoming"
        if can_publish_now:
            ready_now_count += 1
        elif should_wait_for_window:
            wait_window_count += 1
        else:
            manual_count += 1
        cast(dict[str, Any], policy["platforms"])[platform] = {
            "post_status": post_result.get("status", "pending"),
            "browser_status": browser.get("status", "unknown"),
            "timing_status": timing_status,
            "next_window": timing_payload.get("window", ""),
            "next_start_local": timing_payload.get("next_start_local", ""),
            "can_publish_now": can_publish_now,
            "should_wait_for_window": should_wait_for_window,
            "blockers": platform_blockers,
        }

    total = len(run.get("platform_targets") or [])
    if (run.get("status") or "") == "posted" and total:
        policy["decision"] = "completed"
        policy["reason"] = "all_target_platforms_posted"
    elif blocking_reasons:
        if "approval_missing" in blocking_reasons:
            policy["decision"] = "hold_for_approval"
            policy["reason"] = "approval_required_before_publish"
        else:
            policy["decision"] = "manual_intervention_required"
            policy["reason"] = ",".join(blocking_reasons)
    elif total and ready_now_count == total:
        policy["decision"] = (
            "ready_for_auto_publish"
            if config.auto_publish_enabled
            else "ready_for_supervised_publish"
        )
        policy["reason"] = "all_platforms_ready_now"
    elif total and ready_now_count + wait_window_count == total and wait_window_count > 0:
        policy["decision"] = "wait_for_window"
        policy["reason"] = "one_or_more_platforms_outside_recommended_window"
    else:
        policy["decision"] = "manual_intervention_required"
        policy["reason"] = "browser_or_runtime_readiness_gap"
    return policy


def render_execution_policy(run: dict[str, Any]) -> str:
    policy = (run.get("analysis") or {}).get("execution_policy") or {}
    lines = [
        "# Execution Policy",
        "",
        f"Decision: {policy.get('decision', 'unknown')}",
        f"Reason: {policy.get('reason', '') or 'unknown'}",
        f"Approval state: {policy.get('approval_state', '')}",
        f"Publish ready: {policy.get('publish_ready', False)}",
        f"Confidence: {policy.get('confidence', 'n/a')} / threshold={policy.get('threshold', 'n/a')}",
        f"Auto publish enabled: {policy.get('auto_publish_enabled', False)}",
        "",
        "Platform execution state:",
    ]
    platforms = policy.get("platforms") or {}
    if not platforms:
        lines.append("- None")
    else:
        for platform, payload in platforms.items():
            blockers = ", ".join(payload.get("blockers") or []) or "none"
            lines.append(
                f"- {platform}: can_publish_now={payload.get('can_publish_now', False)} | wait_for_window={payload.get('should_wait_for_window', False)} | browser={payload.get('browser_status', 'unknown')} | timing={payload.get('timing_status', 'unknown')} | blockers={blockers}"
            )
    return "\n".join(lines) + "\n"


def build_proof_readiness_payload(run: dict[str, Any]) -> dict[str, Any]:
    simulation_enabled = bool((run.get("simulation") or {}).get("enabled"))
    prompt_actionable = prompt_is_actionable(str(run.get("prompt") or "").strip())
    approval_state = str(run.get("approval_state") or "").strip().lower() or "pending"
    status = str(run.get("status") or "").strip().lower() or "unknown"
    platform_targets = list(run.get("platform_targets") or [])
    post_results = run.get("post_results") or {}
    posted_platforms = []
    failed_platforms = []
    pending_platforms = []
    reason_codes = []
    recommended_actions = []

    for platform in platform_targets:
        platform_status = (
            str((post_results.get(platform) or {}).get("status") or "").strip().lower()
        )
        if platform_status == "posted":
            posted_platforms.append(platform)
        elif platform_status == "failed":
            failed_platforms.append(platform)
        else:
            pending_platforms.append(platform)

    if simulation_enabled:
        reason_codes.append("simulation_run")
        recommended_actions.append("Use a non-simulated run for live proof.")
    if not prompt_actionable:
        reason_codes.append("prompt_not_actionable")
        recommended_actions.append(
            "Replace the placeholder daily prompt or promote a ready queued brief."
        )
    if approval_state == "rejected":
        reason_codes.append("approval_rejected")
        recommended_actions.append(
            "Create or promote a new brief instead of relying on the rejected run."
        )
    if status in {"failed", "rejected"}:
        reason_codes.append("run_terminal_failure")
        recommended_actions.append("Inspect the run notes and rebuild the run from a clean brief.")

    proof_eligible = not reason_codes
    live_proof_complete = bool(
        proof_eligible and platform_targets and len(posted_platforms) == len(platform_targets)
    )

    if live_proof_complete:
        next_action = "proof_complete"
    elif failed_platforms:
        next_action = "retry_failed_platforms"
    elif proof_eligible and approval_state == "pending":
        next_action = "approve_or_reject"
        recommended_actions.append("Approve or reject the run from Telegram before publishing.")
    elif proof_eligible and approval_state == "approved" and pending_platforms:
        next_action = "publish_remaining_platforms"
        recommended_actions.append(
            "Publish the remaining approved platform drafts with the browser automation flow."
        )
    elif proof_eligible:
        next_action = "review_operator_state"
    else:
        next_action = "replace_or_archive_run"

    return {
        "proof_eligible": proof_eligible,
        "live_proof_complete": live_proof_complete,
        "reason_codes": reason_codes,
        "posted_platforms": posted_platforms,
        "failed_platforms": failed_platforms,
        "pending_platforms": pending_platforms,
        "next_action": next_action,
        "recommended_actions": list(dict.fromkeys(recommended_actions)),
        "approval_state": approval_state,
        "status": status,
    }


def render_proof_readiness(run: dict[str, Any]) -> str:
    readiness = (run.get("analysis") or {}).get("proof_readiness") or {}
    lines = [
        "# Proof Readiness",
        "",
        f"Proof eligible: {readiness.get('proof_eligible', False)}",
        f"Live proof complete: {readiness.get('live_proof_complete', False)}",
        f"Approval state: {readiness.get('approval_state', '')}",
        f"Run status: {readiness.get('status', '')}",
        f"Next action: {readiness.get('next_action', 'unknown')}",
        "",
        f"Reason codes: {', '.join(readiness.get('reason_codes') or []) or 'none'}",
        f"Posted platforms: {', '.join(readiness.get('posted_platforms') or []) or 'none'}",
        f"Failed platforms: {', '.join(readiness.get('failed_platforms') or []) or 'none'}",
        f"Pending platforms: {', '.join(readiness.get('pending_platforms') or []) or 'none'}",
        "",
        "Recommended actions:",
    ]
    actions = readiness.get("recommended_actions") or []
    if not actions:
        lines.append("- None")
    else:
        lines.extend(f"- {item}" for item in actions)
    return "\n".join(lines) + "\n"


def synthesize_hybrid_context(config: AppConfig, run_id: str) -> dict[str, Any]:
    store = RunStore(config)
    run = store.load_run(run_id)
    hybrid = build_hybrid_context_payload(run)
    run["analysis"] = {
        **dict(run.get("analysis") or {}),
        "hybrid_context": hybrid,
    }
    _timestamp_run(run, "hybrid_context_synthesized")
    run["updated_at"] = now_utc()
    store.save_run(run)
    outputs = run.get("outputs") or {}
    store.save_text_artifact(
        run_id,
        outputs.get("hybrid_context", "artifacts/hybrid_context.md"),
        render_hybrid_context(run),
    )
    store.save_text_artifact(
        run_id,
        outputs.get("researcher_agent", "ops/agents/researcher.md"),
        render_researcher_report(run),
    )
    store.save_text_artifact(
        run_id, outputs.get("analyzer_agent", "ops/agents/analyzer.md"), render_analyzer_report(run)
    )
    return run


def package_run(config: AppConfig, run_id: str) -> dict[str, Any]:
    store = RunStore(config)
    run = store.load_run(run_id)
    if not run.get("keywords"):
        article_title = (run.get("article") or {}).get("title", "")
        article_summary = (run.get("article") or {}).get("summary", "")
        run["keywords"] = derive_keywords(run["prompt"], article_title, article_summary)
    analysis = _analysis_payload(run)
    analysis["brand_profile"] = load_brand_profile_text(config)
    if not analysis.get("hybrid_context"):
        analysis["hybrid_context"] = build_hybrid_context_payload(run)
    run["identity"] = cast(dict[str, Any], build_run_identity(run))
    run["proof_pack"] = _proof_pack_for_run(config, run)
    run["confidence"] = compute_confidence(run)
    run["approval_state"] = "pending" if config.approval_required else "approved"
    run["status"] = "awaiting_approval" if config.approval_required else "approved"
    _timestamp_run(run, "packaged")
    analysis["evidence_pack"] = build_evidence_pack_payload(run)
    analysis["publish_strategy"] = build_publish_strategy_payload(config, run)
    analysis["recovery_plan"] = build_recovery_plan_payload(config, run)
    analysis["publication_timing"] = build_publication_timing_payload(config, run)
    analysis["provenance_manifest"] = build_provenance_manifest_payload(config, run)
    analysis["approval_packet"] = build_approval_packet_payload(config, run)
    analysis["execution_policy"] = build_execution_policy_payload(config, run)
    _refresh_analysis_runtime_payloads(config, run)
    run["updated_at"] = now_utc()

    outputs = {
        "brief": "prompts/openclaw_generation_brief.md",
        "review": "prompts/openclaw_review_checklist.md",
        "review_report": "ops/review_report.md",
        "telegram": "ops/telegram_preview.md",
        "article": "drafts/article.md",
        "linkedin": "drafts/linkedin.md",
        "facebook": "drafts/facebook.md",
        "x": "drafts/x.md",
        "image": "drafts/image_prompt.md",
        "image_asset": "media/social_card.png",
        "posting": "ops/posting_checklist.md",
        "memory_context": "artifacts/memory_context.md",
        "graph_context": "artifacts/graph_context.md",
        "hybrid_context": "artifacts/hybrid_context.md",
        "source_safety": "artifacts/source_safety.md",
        "evidence_pack": "artifacts/evidence_pack.md",
        "publication_timing": "ops/publication_timing.md",
        "provenance_manifest": "ops/provenance_manifest.md",
        "source_registry": "ops/source_registry.md",
        "approval_packet": "ops/approval_packet.md",
        "execution_policy": "ops/execution_policy.md",
        "proof_readiness": "ops/proof_readiness.md",
        "proof_pack": "ops/proof_pack.md",
        "delivery_pipeline": "ops/delivery_pipeline.md",
        "zero_cost_proof": "ops/zero_cost_proof.json",
        "publish_strategy": "ops/publish_strategy.md",
        "recovery_plan": "ops/recovery_plan.md",
        "researcher_agent": "ops/agents/researcher.md",
        "analyzer_agent": "ops/agents/analyzer.md",
        "writer_agent": "ops/agents/writer.md",
        "editor_agent": "ops/agents/editor.md",
        "publisher_agent": "ops/agents/publisher.md",
    }
    run["outputs"] = outputs
    store.save_run(run)
    store.save_text_artifact(run_id, outputs["brief"], build_generation_brief(run))
    store.save_text_artifact(run_id, outputs["review"], build_review_checklist(run))
    store.save_text_artifact(
        run_id, outputs["review_report"], "# Review Report\n\nPending automatic review.\n"
    )
    store.save_text_artifact(run_id, outputs["telegram"], build_telegram_preview(run))
    store.save_text_artifact(run_id, outputs["article"], "# Article Draft\n\nPending generation.\n")
    store.save_text_artifact(
        run_id, outputs["linkedin"], "# LinkedIn Draft\n\nPending generation.\n"
    )
    store.save_text_artifact(
        run_id, outputs["facebook"], "# Facebook Draft\n\nPending generation.\n"
    )
    store.save_text_artifact(run_id, outputs["x"], "# X Draft\n\nPending generation.\n")
    store.save_text_artifact(run_id, outputs["image"], "# Image Prompt\n\nPending generation.\n")
    store.save_text_artifact(run_id, outputs["posting"], build_posting_checklist(run))
    store.save_text_artifact(run_id, outputs["memory_context"], render_memory_context(run))
    store.save_text_artifact(run_id, outputs["graph_context"], render_graph_context(run))
    store.save_text_artifact(run_id, outputs["hybrid_context"], render_hybrid_context(run))
    store.save_text_artifact(run_id, outputs["source_safety"], render_source_safety_report(run))
    store.save_text_artifact(run_id, outputs["evidence_pack"], render_evidence_pack(run))
    store.save_text_artifact(run_id, outputs["publication_timing"], render_publication_timing(run))
    store.save_text_artifact(
        run_id, outputs["provenance_manifest"], render_provenance_manifest(run)
    )
    store.save_text_artifact(run_id, outputs["source_registry"], render_source_registry(run))
    store.save_text_artifact(run_id, outputs["approval_packet"], render_approval_packet(run))
    store.save_text_artifact(run_id, outputs["execution_policy"], render_execution_policy(run))
    store.save_text_artifact(run_id, outputs["proof_readiness"], render_proof_readiness(run))
    store.save_text_artifact(run_id, outputs["proof_pack"], render_proof_pack(run))
    store.save_text_artifact(run_id, outputs["delivery_pipeline"], render_delivery_pipeline(run))
    store.save_text_artifact(run_id, outputs["zero_cost_proof"], render_zero_cost_proof(run))
    store.save_text_artifact(run_id, outputs["publish_strategy"], render_publish_strategy(run))
    store.save_text_artifact(run_id, outputs["recovery_plan"], render_recovery_plan(run))
    store.save_text_artifact(run_id, outputs["researcher_agent"], render_researcher_report(run))
    store.save_text_artifact(run_id, outputs["analyzer_agent"], render_analyzer_report(run))
    store.save_text_artifact(run_id, outputs["writer_agent"], render_writer_report(run))
    store.save_text_artifact(run_id, outputs["editor_agent"], render_editor_agent_report(run))
    store.save_text_artifact(run_id, outputs["publisher_agent"], render_publisher_report(run))
    generate_image_asset(config, run_id)
    return run


def generate_drafts(config: AppConfig, run_id: str) -> dict[str, Any]:
    store = RunStore(config)
    run = store.load_run(run_id)
    outputs = run.get("outputs") or {}
    memory_hits = list(run.get("memory_hits") or [])

    drafts = {
        outputs.get("article", "drafts/article.md"): build_article_draft(run, memory_hits),
        outputs.get("linkedin", "drafts/linkedin.md"): build_linkedin_draft(run, memory_hits),
        outputs.get("facebook", "drafts/facebook.md"): build_facebook_draft(run, memory_hits),
        outputs.get("x", "drafts/x.md"): build_x_draft(run, memory_hits),
        outputs.get("image", "drafts/image_prompt.md"): build_image_prompt(run, memory_hits),
    }
    for rel_path, content in drafts.items():
        store.save_text_artifact(run_id, rel_path, content)

    _timestamp_run(run, "drafted")
    run["analysis"] = {
        **dict(run.get("analysis") or {}),
        "delivery_pipeline": build_delivery_pipeline_payload(config, run),
    }
    run["identity"] = cast(dict[str, Any], build_run_identity(run))
    run["proof_pack"] = _proof_pack_for_run(config, run)
    run["updated_at"] = now_utc()
    store.save_run(run)
    generate_image_asset(config, run_id)
    store.save_text_artifact(
        run_id, outputs.get("proof_pack", "ops/proof_pack.md"), render_proof_pack(run)
    )
    store.save_text_artifact(
        run_id, outputs.get("writer_agent", "ops/agents/writer.md"), render_writer_report(run)
    )
    return run


def review_run(config: AppConfig, run_id: str) -> dict[str, Any]:
    store = RunStore(config)
    run = store.load_run(run_id)
    run_dir = store.run_dir(run_id)
    analysis = _analysis_payload(run)
    analysis["portfolio_diversity"] = _portfolio_diversity_analysis(config, run)
    analysis["hybrid_context"] = build_hybrid_context_payload(run)
    report = evaluate_run_quality(config, run, run_dir)
    run["quality_gate"] = report
    run["confidence"] = compute_confidence(run)
    report["publish_ready"] = bool(
        report.get("overall_pass") and _confidence_value(run) >= config.confidence_threshold
    )
    run["quality_gate"] = report
    analysis["evidence_pack"] = build_evidence_pack_payload(run)
    analysis["publish_strategy"] = build_publish_strategy_payload(config, run)
    analysis["recovery_plan"] = build_recovery_plan_payload(config, run)
    analysis["publication_timing"] = build_publication_timing_payload(config, run)
    analysis["provenance_manifest"] = build_provenance_manifest_payload(config, run)
    analysis["approval_packet"] = build_approval_packet_payload(config, run)
    analysis["execution_policy"] = build_execution_policy_payload(config, run)
    run["identity"] = cast(dict[str, Any], build_run_identity(run))
    run["proof_pack"] = _proof_pack_for_run(config, run)
    _timestamp_run(run, "reviewed")
    _refresh_analysis_runtime_payloads(config, run)
    run["updated_at"] = now_utc()
    store.save_run(run)
    store.save_text_artifact(run_id, run["outputs"]["review_report"], render_review_report(run))
    store.save_text_artifact(run_id, run["outputs"]["telegram"], build_telegram_preview(run))
    store.save_text_artifact(
        run_id,
        run["outputs"].get("hybrid_context", "artifacts/hybrid_context.md"),
        render_hybrid_context(run),
    )
    store.save_text_artifact(
        run_id,
        run["outputs"].get("evidence_pack", "artifacts/evidence_pack.md"),
        render_evidence_pack(run),
    )
    store.save_text_artifact(
        run_id,
        run["outputs"].get("publication_timing", "ops/publication_timing.md"),
        render_publication_timing(run),
    )
    store.save_text_artifact(
        run_id,
        run["outputs"].get("provenance_manifest", "ops/provenance_manifest.md"),
        render_provenance_manifest(run),
    )
    store.save_text_artifact(
        run_id,
        run["outputs"].get("source_registry", "ops/source_registry.md"),
        render_source_registry(run),
    )
    store.save_text_artifact(
        run_id,
        run["outputs"].get("approval_packet", "ops/approval_packet.md"),
        render_approval_packet(run),
    )
    store.save_text_artifact(
        run_id,
        run["outputs"].get("execution_policy", "ops/execution_policy.md"),
        render_execution_policy(run),
    )
    store.save_text_artifact(
        run_id,
        run["outputs"].get("proof_readiness", "ops/proof_readiness.md"),
        render_proof_readiness(run),
    )
    store.save_text_artifact(
        run_id, run["outputs"].get("proof_pack", "ops/proof_pack.md"), render_proof_pack(run)
    )
    store.save_text_artifact(
        run_id,
        run["outputs"].get("delivery_pipeline", "ops/delivery_pipeline.md"),
        render_delivery_pipeline(run),
    )
    store.save_text_artifact(
        run_id,
        run["outputs"].get("publish_strategy", "ops/publish_strategy.md"),
        render_publish_strategy(run),
    )
    store.save_text_artifact(
        run_id,
        run["outputs"].get("recovery_plan", "ops/recovery_plan.md"),
        render_recovery_plan(run),
    )
    store.save_text_artifact(
        run_id,
        run["outputs"].get("analyzer_agent", "ops/agents/analyzer.md"),
        render_analyzer_report(run),
    )
    store.save_text_artifact(
        run_id,
        run["outputs"].get("editor_agent", "ops/agents/editor.md"),
        render_editor_agent_report(run),
    )
    store.save_text_artifact(
        run_id,
        run["outputs"].get("publisher_agent", "ops/agents/publisher.md"),
        render_publisher_report(run),
    )
    return run


def sync_memory(config: AppConfig, run_id: str) -> dict[str, Any]:
    payload = sync_run_memory(config, run_id)
    store = RunStore(config)
    run = store.load_run(run_id)
    run["analysis"] = {
        **dict(run.get("analysis") or {}),
        "memory_index": payload,
    }
    run["identity"] = cast(dict[str, Any], build_run_identity(run))
    run["proof_pack"] = _proof_pack_for_run(config, run)
    _timestamp_run(run, "memory_synced")
    run["updated_at"] = now_utc()
    store.save_run(run)
    outputs = run.get("outputs") or {}
    store.save_text_artifact(
        run_id, outputs.get("proof_pack", "ops/proof_pack.md"), render_proof_pack(run)
    )
    store.save_text_artifact(
        run_id,
        outputs.get("publisher_agent", "ops/agents/publisher.md"),
        render_publisher_report(run),
    )
    return run


def sync_graph(config: AppConfig, run_id: str) -> dict[str, Any]:
    payload = sync_run_graph(config, run_id)
    store = RunStore(config)
    run = load_knowledge_graph_context(config, run_id)
    run["analysis"] = {
        **dict(run.get("analysis") or {}),
        "graph_index": payload,
    }
    run["identity"] = cast(dict[str, Any], build_run_identity(run))
    run["proof_pack"] = _proof_pack_for_run(config, run)
    _timestamp_run(run, "graph_synced")
    run["updated_at"] = now_utc()
    store.save_run(run)
    graph_context_path = (run.get("outputs") or {}).get(
        "graph_context", "artifacts/graph_context.md"
    )
    store.save_text_artifact(run_id, graph_context_path, render_graph_context(run))
    outputs = run.get("outputs") or {}
    store.save_text_artifact(
        run_id, outputs.get("proof_pack", "ops/proof_pack.md"), render_proof_pack(run)
    )
    store.save_text_artifact(
        run_id,
        outputs.get("publisher_agent", "ops/agents/publisher.md"),
        render_publisher_report(run),
    )
    return run


def approve_run(
    config: AppConfig, run_id: str, note: str = "", force: bool = False, operator_id: str = ""
) -> dict[str, Any]:
    store = RunStore(config)
    run = store.load_run(run_id)
    run["approval_state"] = "approved"
    run["status"] = "approved"
    if operator_id:
        run["operator_resolution"] = {
            "operator_id": operator_id,
            "resolution_at": now_utc(),
            "note": note,
            "decision": "approved",
        }
    if note:
        run["notes"].append(note)
    if force:
        # Operator explicitly overrides the quality gate â€” stamp the override flag so
        # prepare_publish knows it is safe to proceed even when publish_ready is False.
        qg = dict(run.get("quality_gate") or {})
        qg["quality_gate_override"] = True
        run["quality_gate"] = qg
    run["analysis"] = {
        **dict(run.get("analysis") or {}),
        "proof_readiness": build_proof_readiness_payload(run),
        "delivery_pipeline": build_delivery_pipeline_payload(config, run),
    }
    run["identity"] = cast(dict[str, Any], build_run_identity(run))
    run["proof_pack"] = _proof_pack_for_run(config, run)
    _timestamp_run(run, "approved")
    run["updated_at"] = now_utc()
    store.save_run(run)
    outputs = run.get("outputs") or {}
    store.save_text_artifact(
        run_id,
        outputs.get("provenance_manifest", "ops/provenance_manifest.md"),
        render_provenance_manifest(run),
    )
    store.save_text_artifact(
        run_id, outputs.get("proof_pack", "ops/proof_pack.md"), render_proof_pack(run)
    )
    store.save_text_artifact(
        run_id,
        outputs.get("proof_readiness", "ops/proof_readiness.md"),
        render_proof_readiness(run),
    )
    store.save_text_artifact(
        run_id,
        outputs.get("delivery_pipeline", "ops/delivery_pipeline.md"),
        render_delivery_pipeline(run),
    )

    thread_id = run.get("thread_id")
    if thread_id:
        try:
            from .langgraph_workflow import resume_pipeline
            return resume_pipeline(config, thread_id)
        except Exception as exc:
            run["notes"].append(f"LangGraph resume failed: {exc}")
            store.save_run(run)

    return run


def reject_run(
    config: AppConfig, run_id: str, note: str = "", operator_id: str = ""
) -> dict[str, Any]:
    store = RunStore(config)
    run = store.load_run(run_id)
    run["approval_state"] = "rejected"
    run["status"] = "rejected"
    if operator_id:
        run["operator_resolution"] = {
            "operator_id": operator_id,
            "resolution_at": now_utc(),
            "note": note,
            "decision": "rejected",
        }
    if note:
        run["notes"].append(note)
    run["analysis"] = {
        **dict(run.get("analysis") or {}),
        "proof_readiness": build_proof_readiness_payload(run),
        "delivery_pipeline": build_delivery_pipeline_payload(config, run),
    }
    run["identity"] = cast(dict[str, Any], build_run_identity(run))
    run["proof_pack"] = _proof_pack_for_run(config, run)
    _timestamp_run(run, "rejected")
    run["updated_at"] = now_utc()
    store.save_run(run)
    outputs = run.get("outputs") or {}
    store.save_text_artifact(
        run_id,
        outputs.get("provenance_manifest", "ops/provenance_manifest.md"),
        render_provenance_manifest(run),
    )
    store.save_text_artifact(
        run_id, outputs.get("proof_pack", "ops/proof_pack.md"), render_proof_pack(run)
    )
    store.save_text_artifact(
        run_id,
        outputs.get("proof_readiness", "ops/proof_readiness.md"),
        render_proof_readiness(run),
    )
    store.save_text_artifact(
        run_id,
        outputs.get("delivery_pipeline", "ops/delivery_pipeline.md"),
        render_delivery_pipeline(run),
    )

    thread_id = run.get("thread_id")
    if thread_id:
        try:
            from .langgraph_workflow import resume_pipeline
            return resume_pipeline(config, thread_id)
        except Exception as exc:
            run["notes"].append(f"LangGraph resume (reject) failed: {exc}")
            store.save_run(run)

    return run


def replay_run(config: AppConfig, run_id: str, node_name: str) -> dict[str, Any]:
    """Replay a specific pipeline node on operator request (Â§23.5)."""
    store = RunStore(config)
    run = store.load_run(run_id)
    thread_id = run.get("thread_id")
    if not thread_id:
        raise ValueError(f"Run {run_id} has no thread_id â€” cannot replay.")

    from .langgraph_workflow import replay_node
    return replay_node(config, thread_id, node_name)


def mark_preview_sent(config: AppConfig, run_id: str, telegram_route: str = "") -> dict[str, Any]:
    store = RunStore(config)
    run = store.load_run(run_id)
    if telegram_route:
        run["telegram_route"] = telegram_route
    run["telegram_preview_sent_at"] = now_utc()
    _timestamp_run(run, "preview_sent")
    run["analysis"] = {
        **dict(run.get("analysis") or {}),
        "delivery_pipeline": build_delivery_pipeline_payload(config, run),
    }
    run["identity"] = cast(dict[str, Any], build_run_identity(run))
    run["proof_pack"] = _proof_pack_for_run(config, run)
    run["updated_at"] = now_utc()
    store.save_run(run)
    outputs = run.get("outputs") or {}
    store.save_text_artifact(
        run_id,
        outputs.get("provenance_manifest", "ops/provenance_manifest.md"),
        render_provenance_manifest(run),
    )
    store.save_text_artifact(
        run_id, outputs.get("proof_pack", "ops/proof_pack.md"), render_proof_pack(run)
    )
    store.save_text_artifact(
        run_id,
        outputs.get("delivery_pipeline", "ops/delivery_pipeline.md"),
        render_delivery_pipeline(run),
    )
    return run


def maybe_send_telegram_preview(
    config: AppConfig, run_id: str, force: bool = False
) -> dict[str, Any]:
    store = RunStore(config)
    run = store.load_run(run_id)
    if not config.telegram_target:
        return {
            "run_id": run_id,
            "status": "skipped",
            "reason": "telegram_target_missing",
        }
    if not config.telegram_bot_token:
        return {
            "run_id": run_id,
            "status": "skipped",
            "reason": "telegram_bot_token_missing",
        }
    if run.get("telegram_preview_sent_at") and not force:
        return {
            "run_id": run_id,
            "status": "skipped",
            "reason": "preview_already_sent",
            "telegram_preview_sent_at": run.get("telegram_preview_sent_at", ""),
        }

    payload = send_run_preview(config, run_id)
    mark_preview_sent(config, run_id, telegram_route=config.telegram_target or "")
    latest = store.load_run(run_id)
    return {
        "run_id": run_id,
        "status": "sent",
        "chat_id": payload.get("chat_id", ""),
        "message_id": payload.get("message_id"),
        "telegram_preview_sent_at": latest.get("telegram_preview_sent_at", ""),
    }


def pause_automation(
    config: AppConfig, reason: str = "", updated_by: str = "operator"
) -> dict[str, Any]:
    store = RunStore(config)
    control = store.load_control()
    control["automation_paused"] = True
    control["reason"] = reason
    control["updated_by"] = updated_by
    control["updated_at"] = now_utc()
    store.save_control(control)
    return control


def resume_automation(
    config: AppConfig, reason: str = "", updated_by: str = "operator"
) -> dict[str, Any]:
    store = RunStore(config)
    control = store.load_control()
    control["automation_paused"] = False
    control["reason"] = reason
    control["updated_by"] = updated_by
    control["updated_at"] = now_utc()
    store.save_control(control)
    return control


def fail_run(config: AppConfig, run_id: str, reason: str) -> dict[str, Any]:
    store = RunStore(config)
    run = store.load_run(run_id)
    run["status"] = "failed"
    if reason:
        run["notes"].append(reason)
    run["analysis"] = {
        **dict(run.get("analysis") or {}),
        "proof_readiness": build_proof_readiness_payload(run),
        "delivery_pipeline": build_delivery_pipeline_payload(config, run),
    }
    run["identity"] = cast(dict[str, Any], build_run_identity(run))
    run["proof_pack"] = _proof_pack_for_run(config, run)
    _timestamp_run(run, "failed")
    run["updated_at"] = now_utc()
    store.save_run(run)
    outputs = run.get("outputs") or {}
    store.save_text_artifact(
        run_id, outputs.get("proof_pack", "ops/proof_pack.md"), render_proof_pack(run)
    )
    store.save_text_artifact(
        run_id,
        outputs.get("proof_readiness", "ops/proof_readiness.md"),
        render_proof_readiness(run),
    )
    store.save_text_artifact(
        run_id,
        outputs.get("delivery_pipeline", "ops/delivery_pipeline.md"),
        render_delivery_pipeline(run),
    )
    return run


def prepare_publish(
    config: AppConfig, run_id: str, platform: str, force: bool = False
) -> dict[str, Any]:
    store = RunStore(config)
    run = store.load_run(run_id)
    if run["approval_state"] != "approved":
        raise ValueError(f"Run {run_id} is not approved.")
    if platform not in run["platform_targets"]:
        raise ValueError(f"Platform {platform} is not configured for run {run_id}.")
    quality_gate = run.get("quality_gate") or {}
    gate_overridden = bool(quality_gate.get("quality_gate_override", False)) or force
    confidence = _confidence_value(run)
    if not gate_overridden and confidence < config.confidence_threshold:
        raise ValueError(
            f"Run {run_id} confidence {confidence} is below threshold {config.confidence_threshold}."
        )
    if not gate_overridden and not quality_gate.get("publish_ready", False):
        raise ValueError(f"Run {run_id} is not publish-ready according to the review gate.")

    result = run["post_results"][platform]
    result["attempts"] += 1
    result["status"] = "ready_to_publish"
    result["updated_at"] = now_utc()
    run["status"] = recompute_run_status(run)
    analysis = _analysis_payload(run)
    analysis["publish_strategy"] = build_publish_strategy_payload(config, run)
    analysis["recovery_plan"] = build_recovery_plan_payload(config, run)
    analysis["publication_timing"] = build_publication_timing_payload(config, run)
    analysis["provenance_manifest"] = build_provenance_manifest_payload(config, run)
    analysis["approval_packet"] = build_approval_packet_payload(config, run)
    analysis["execution_policy"] = build_execution_policy_payload(config, run)
    _refresh_analysis_runtime_payloads(config, run)
    run["identity"] = cast(dict[str, Any], build_run_identity(run))
    run["proof_pack"] = _proof_pack_for_run(config, run)
    platform_plan = (
        ((run.get("analysis") or {}).get("publish_strategy") or {})
        .get("platforms", {})
        .get(platform, {})
    )
    browser_health = platform_plan.get("browser_health") or {}
    if browser_health and browser_health.get("status") != "ready":
        raise ValueError(
            f"Browser profile for {platform} is not ready: {browser_health.get('reason') or browser_health.get('status')}."
        )
    _timestamp_run(run, f"publish_prepared_{platform}")
    run["updated_at"] = now_utc()
    store.save_run(run)
    outputs = run.get("outputs") or {}
    store.save_text_artifact(
        run_id,
        outputs.get("publication_timing", "ops/publication_timing.md"),
        render_publication_timing(run),
    )
    store.save_text_artifact(
        run_id,
        outputs.get("provenance_manifest", "ops/provenance_manifest.md"),
        render_provenance_manifest(run),
    )
    store.save_text_artifact(
        run_id,
        outputs.get("source_registry", "ops/source_registry.md"),
        render_source_registry(run),
    )
    store.save_text_artifact(
        run_id,
        outputs.get("approval_packet", "ops/approval_packet.md"),
        render_approval_packet(run),
    )
    store.save_text_artifact(
        run_id,
        outputs.get("execution_policy", "ops/execution_policy.md"),
        render_execution_policy(run),
    )
    store.save_text_artifact(
        run_id,
        outputs.get("proof_readiness", "ops/proof_readiness.md"),
        render_proof_readiness(run),
    )
    store.save_text_artifact(
        run_id, outputs.get("proof_pack", "ops/proof_pack.md"), render_proof_pack(run)
    )
    store.save_text_artifact(
        run_id,
        outputs.get("delivery_pipeline", "ops/delivery_pipeline.md"),
        render_delivery_pipeline(run),
    )
    store.save_text_artifact(
        run_id,
        outputs.get("publish_strategy", "ops/publish_strategy.md"),
        render_publish_strategy(run),
    )
    store.save_text_artifact(
        run_id, outputs.get("recovery_plan", "ops/recovery_plan.md"), render_recovery_plan(run)
    )
    store.save_text_artifact(
        run_id,
        outputs.get("publisher_agent", "ops/agents/publisher.md"),
        render_publisher_report(run),
    )

    draft_path = store.run_dir(run_id) / "drafts" / f"{platform}.md"
    image_path = find_first_existing_image(store.run_dir(run_id))
    return {
        "run_id": run_id,
        "platform": platform,
        "status": run["status"],
        "approval_state": run["approval_state"],
        "attempt": result["attempts"],
        "browser_profile": result["browser_profile"],
        "compose_url": compose_url_for(config, platform),
        "draft_path": str(draft_path),
        "image_path": str(image_path) if image_path else "",
        "posting_checklist_path": str(store.run_dir(run_id) / "ops" / "posting_checklist.md"),
        "browser_health": browser_health,
        "disclosure_text": platform_plan.get("disclosure_text", ""),
        "attribution_hint": platform_plan.get("attribution_hint", ""),
        "call_to_action": platform_plan.get("call_to_action", ""),
    }


def prepare_publish_all(config: AppConfig, run_id: str) -> dict[str, Any]:
    store = RunStore(config)
    run = store.load_run(run_id)
    prepared = [prepare_publish(config, run_id, platform) for platform in run["platform_targets"]]
    latest = store.load_run(run_id)
    return {
        "run_id": run_id,
        "status": latest["status"],
        "approval_state": latest["approval_state"],
        "targets": prepared,
    }


def record_post_result(
    config: AppConfig,
    run_id: str,
    platform: str,
    status: str,
    url: str = "",
    note: str = "",
    screenshots: list[str] | None = None,
    failure_reason: str = "",
) -> dict[str, Any]:
    store = RunStore(config)
    run = store.load_run(run_id)
    result = run.setdefault("post_results", {}).setdefault(platform, {})
    result["status"] = status
    result["url"] = url
    result["note"] = note
    result["failure_reason"] = failure_reason
    if screenshots is not None:
        result["screenshots"] = list(screenshots)
    result["updated_at"] = now_utc()
    if status == "posted":
        _timestamp_run(run, f"posted_{platform}")
    if status == "failed":
        _timestamp_run(run, f"failed_{platform}")
    run["status"] = recompute_run_status(run)
    analysis = _analysis_payload(run)
    analysis["publish_strategy"] = build_publish_strategy_payload(config, run)
    analysis["recovery_plan"] = build_recovery_plan_payload(config, run)
    analysis["publication_timing"] = build_publication_timing_payload(config, run)
    analysis["provenance_manifest"] = build_provenance_manifest_payload(config, run)
    analysis["approval_packet"] = build_approval_packet_payload(config, run)
    analysis["execution_policy"] = build_execution_policy_payload(config, run)
    _refresh_analysis_runtime_payloads(config, run)
    run["identity"] = cast(dict[str, Any], build_run_identity(run))
    run["proof_pack"] = _proof_pack_for_run(config, run)
    run["updated_at"] = now_utc()
    store.save_run(run)
    outputs = run.get("outputs") or {}
    store.save_text_artifact(
        run_id,
        outputs.get("publication_timing", "ops/publication_timing.md"),
        render_publication_timing(run),
    )
    store.save_text_artifact(
        run_id,
        outputs.get("provenance_manifest", "ops/provenance_manifest.md"),
        render_provenance_manifest(run),
    )
    store.save_text_artifact(
        run_id,
        outputs.get("source_registry", "ops/source_registry.md"),
        render_source_registry(run),
    )
    store.save_text_artifact(
        run_id,
        outputs.get("approval_packet", "ops/approval_packet.md"),
        render_approval_packet(run),
    )
    store.save_text_artifact(
        run_id,
        outputs.get("execution_policy", "ops/execution_policy.md"),
        render_execution_policy(run),
    )
    store.save_text_artifact(
        run_id,
        outputs.get("proof_readiness", "ops/proof_readiness.md"),
        render_proof_readiness(run),
    )
    store.save_text_artifact(
        run_id, outputs.get("proof_pack", "ops/proof_pack.md"), render_proof_pack(run)
    )
    store.save_text_artifact(
        run_id,
        outputs.get("delivery_pipeline", "ops/delivery_pipeline.md"),
        render_delivery_pipeline(run),
    )
    store.save_text_artifact(
        run_id,
        outputs.get("publish_strategy", "ops/publish_strategy.md"),
        render_publish_strategy(run),
    )
    store.save_text_artifact(
        run_id, outputs.get("recovery_plan", "ops/recovery_plan.md"), render_recovery_plan(run)
    )
    store.save_text_artifact(
        run_id,
        outputs.get("publisher_agent", "ops/agents/publisher.md"),
        render_publisher_report(run),
    )
    return run


def refresh_run_state(config: AppConfig, run_id: str) -> dict[str, Any]:
    store = RunStore(config)
    run = store.load_run(run_id)
    run["status"] = recompute_run_status(run)
    if run.get("article"):
        run["analysis"] = {
            **dict(run.get("analysis") or {}),
            "source_safety": analyze_source_safety(run["article"]),
        }
    run["analysis"] = {
        **dict(run.get("analysis") or {}),
        "hybrid_context": build_hybrid_context_payload(run)
        if (run.get("analysis") or {}).get("hybrid_context")
        else (run.get("analysis") or {}).get("hybrid_context", {}),
        "evidence_pack": build_evidence_pack_payload(run),
        "publish_strategy": build_publish_strategy_payload(config, run),
        "recovery_plan": build_recovery_plan_payload(config, run),
        "publication_timing": build_publication_timing_payload(config, run),
        "provenance_manifest": build_provenance_manifest_payload(config, run),
        "approval_packet": build_approval_packet_payload(config, run),
        "execution_policy": build_execution_policy_payload(config, run),
        "proof_readiness": build_proof_readiness_payload(run),
        "delivery_pipeline": build_delivery_pipeline_payload(config, run),
    }
    run["identity"] = cast(dict[str, Any], build_run_identity(run))
    run["proof_pack"] = _proof_pack_for_run(config, run)
    run["updated_at"] = now_utc()

    outputs = dict(run.get("outputs") or {})
    outputs.setdefault("delivery_pipeline", "ops/delivery_pipeline.md")
    run["outputs"] = outputs
    store.save_run(run)

    if outputs:
        store.save_text_artifact(
            run_id, outputs.get("telegram", "ops/telegram_preview.md"), build_telegram_preview(run)
        )
        store.save_text_artifact(
            run_id, outputs.get("review_report", "ops/review_report.md"), render_review_report(run)
        )
        store.save_text_artifact(
            run_id,
            outputs.get("source_safety", "artifacts/source_safety.md"),
            render_source_safety_report(run),
        )
        store.save_text_artifact(
            run_id,
            outputs.get("evidence_pack", "artifacts/evidence_pack.md"),
            render_evidence_pack(run),
        )
        store.save_text_artifact(
            run_id,
            outputs.get("publication_timing", "ops/publication_timing.md"),
            render_publication_timing(run),
        )
        store.save_text_artifact(
            run_id,
            outputs.get("provenance_manifest", "ops/provenance_manifest.md"),
            render_provenance_manifest(run),
        )
        store.save_text_artifact(
            run_id,
            outputs.get("source_registry", "ops/source_registry.md"),
            render_source_registry(run),
        )
        store.save_text_artifact(
            run_id,
            outputs.get("approval_packet", "ops/approval_packet.md"),
            render_approval_packet(run),
        )
        store.save_text_artifact(
            run_id,
            outputs.get("execution_policy", "ops/execution_policy.md"),
            render_execution_policy(run),
        )
        store.save_text_artifact(
            run_id,
            outputs.get("proof_readiness", "ops/proof_readiness.md"),
            render_proof_readiness(run),
        )
        store.save_text_artifact(
            run_id, outputs.get("proof_pack", "ops/proof_pack.md"), render_proof_pack(run)
        )
        store.save_text_artifact(
            run_id,
            outputs.get("delivery_pipeline", "ops/delivery_pipeline.md"),
            render_delivery_pipeline(run),
        )
        store.save_text_artifact(
            run_id,
            outputs.get("publish_strategy", "ops/publish_strategy.md"),
            render_publish_strategy(run),
        )
        store.save_text_artifact(
            run_id, outputs.get("recovery_plan", "ops/recovery_plan.md"), render_recovery_plan(run)
        )
        store.save_text_artifact(
            run_id,
            outputs.get("publisher_agent", "ops/agents/publisher.md"),
            render_publisher_report(run),
        )
    return run


def execute_daily_pipeline(
    config: AppConfig,
    prompt: str,
    competitor_url: str,
    keywords: list[str] | None = None,
    targets: list[str] | None = None,
    ignore_pause: bool = False,
    created_at_override: str = "",
    simulation: dict[str, Any] | None = None,
    graph_mode: str = "sequential",
    daily_input: dict[str, Any] | None = None,
    trigger_kind: str = "manual",
    target_language: str = "fr",  # Â§7: Language context
) -> dict[str, Any]:
    # Â§29: Compliance logging
    log_operator_action(config, action="pipeline_started", operator=trigger_kind)

    store = RunStore(config)
    if store.load_control().get("automation_paused") and not ignore_pause:
        raise RuntimeError("Automation is paused. Resume it before running the daily workflow.")
    run = create_run(
        config,
        prompt,
        competitor_url,
        keywords=keywords,
        targets=targets,
        created_at_override=created_at_override,
        simulation=simulation,
        daily_input=daily_input,
        trigger_kind=trigger_kind,
        target_language=target_language,
    )
    run_id = run["run_id"]
    payload = store.load_run(run_id)
    payload["graph_mode"] = graph_mode

    tracker = LatencyTracker()

    tracker.start("acquisition")
    ingest_competitor(config, run_id)
    tracker.stop("acquisition")

    tracker.start("research")
    research_trends(config, run_id)
    tracker.stop("research")

    load_memory_context(config, run_id)
    load_graph_context(config, run_id)
    synthesize_hybrid_context(config, run_id)

    package_run(config, run_id)

    tracker.start("drafting")
    generate_drafts(config, run_id)
    tracker.stop("drafting")

    review_run(config, run_id)

    # Save latency metrics to run record
    payload = store.load_run(run_id)
    if "analysis" not in payload:
        payload["analysis"] = {}
    payload["analysis"]["latency_ms"] = tracker.get_report()
    store.save_run(payload)

    # Â§28: Update comparative analytics
    update_run_analytics(config, run_id)

    sync_memory(config, run_id)
    sync_graph(config, run_id)
    maybe_send_telegram_preview(config, run_id)
    return store.load_run(run_id)


def daily_run(
    config: AppConfig,
    prompt: str,
    competitor_url: str,
    keywords: list[str] | None = None,
    targets: list[str] | None = None,
    ignore_pause: bool = False,
    created_at_override: str = "",
    simulation: dict[str, Any] | None = None,
    daily_input: dict[str, Any] | None = None,
    trigger_kind: str = "manual",
    target_language: str = "fr",  # Â§7: Language context
) -> dict[str, Any]:
    try:
        from .langgraph_workflow import run_langgraph_pipeline

        return run_langgraph_pipeline(
            config,
            prompt=prompt,
            competitor_url=competitor_url,
            keywords=keywords,
            targets=targets,
            ignore_pause=ignore_pause,
            created_at_override=created_at_override,
            simulation=simulation,
            daily_input=daily_input,
            trigger_kind=trigger_kind,
            target_language=target_language,
        )
    except (ImportError, Exception):
        return execute_daily_pipeline(
            config,
            prompt,
            competitor_url,
            keywords=keywords,
            targets=targets,
            ignore_pause=ignore_pause,
            created_at_override=created_at_override,
            simulation=simulation,
            graph_mode="sequential",
            daily_input=daily_input,
            trigger_kind=trigger_kind,
            target_language=target_language,
        )


def save_daily_input(
    config: AppConfig,
    prompt: str,
    competitor_url: str,
    keywords: list[str] | None = None,
    targets: list[str] | None = None,
    target_language: str = "fr",  # Â§7: Language context
) -> dict[str, Any]:
    payload = {
        "prompt": prompt.strip(),
        "competitor_url": competitor_url.strip(),
        "keywords": list(keywords or []),
        "targets": list(targets or config.default_targets),
        "target_language": target_language,
        "updated_at": now_utc(),
    }
    dump_json(config.daily_input_file, payload)
    return payload


def load_daily_input(config: AppConfig) -> dict[str, Any]:
    payload = load_json(config.daily_input_file, default={}) or {}
    normalized = normalize_daily_input_payload(payload, config.default_targets)
    prompt = normalized["prompt"]
    if not prompt and config.default_prompt_file.exists():
        prompt = read_text(config.default_prompt_file).strip()
        normalized["prompt"] = prompt
        normalized["prompt_source"] = "default_prompt_file"
    normalized["resolved_from"] = "daily_input_file"
    normalized["source_file"] = str(config.daily_input_file)
    competitor_url = normalized["competitor_url"]
    if competitor_url and prompt_is_actionable(normalized["prompt"]):
        return normalized

    queued = _select_daily_brief(config)
    if queued:
        return queued

    if not competitor_url:
        raise FileNotFoundError(
            f"Daily competitor URL is missing. Update {config.daily_input_file} or {config.daily_brief_queue_file} before running the scheduled workflow."
        )
    normalized["competitor_url"] = competitor_url
    normalized["prompt"] = prompt
    return normalized


def scheduled_run(config: AppConfig, ignore_pause: bool = False) -> dict[str, Any]:
    payload = load_daily_input(config)
    if not payload["prompt"]:
        raise FileNotFoundError(
            f"Daily prompt is missing. Update {config.daily_input_file}, {config.daily_brief_queue_file}, or {config.default_prompt_file}."
        )
    if not prompt_is_actionable(payload["prompt"]):
        raise ValueError(
            f"Daily prompt still uses unresolved template placeholders. Update {config.daily_input_file} or promote a ready item in {config.daily_brief_queue_file} before running the scheduled workflow."
        )
    run = daily_run(
        config,
        prompt=payload["prompt"],
        competitor_url=payload["competitor_url"],
        keywords=payload["keywords"],
        targets=payload["targets"],
        ignore_pause=ignore_pause,
        daily_input=payload,
        trigger_kind="cron",
    )
    brief_id = str(payload.get("brief_id") or "").strip()
    if brief_id and payload.get("resolved_from") == "daily_brief_queue":
        consume_daily_brief(config, brief_id, run_id=run.get("run_id", ""))
    return run


def generate_image_asset(config: AppConfig, run_id: str) -> dict[str, Any]:
    store = RunStore(config)
    run = store.load_run(run_id)
    output_path = store.run_dir(run_id) / (
        run.get("outputs", {}).get("image_asset") or "media/social_card.png"
    )
    build_social_card(run, output_path, config=config)
    _timestamp_run(run, "image_generated")
    run["analysis"] = {
        **dict(run.get("analysis") or {}),
        "delivery_pipeline": build_delivery_pipeline_payload(config, run),
    }
    run["identity"] = cast(dict[str, Any], build_run_identity(run))
    run["proof_pack"] = _proof_pack_for_run(config, run)
    run["updated_at"] = now_utc()
    store.save_run(run)
    outputs = run.get("outputs") or {}
    store.save_text_artifact(
        run_id,
        outputs.get("provenance_manifest", "ops/provenance_manifest.md"),
        render_source_registry(run),
    )
    store.save_text_artifact(
        run_id, outputs.get("proof_pack", "ops/proof_pack.md"), render_proof_pack(run)
    )
    store.save_text_artifact(
        run_id,
        outputs.get("delivery_pipeline", "ops/delivery_pipeline.md"),
        render_delivery_pipeline(run),
    )
    return {
        "run_id": run_id,
        "image_path": str(output_path),
    }


def doctor_report(config: AppConfig) -> dict[str, Any]:
    store = RunStore(config)
    control = store.load_control()
    daily_input_exists = config.daily_input_file.exists()
    daily_brief_queue = load_daily_brief_queue(config)
    prompt_file_exists = config.default_prompt_file.exists()
    feeds = load_json(config.feeds_file, default={"feeds": []}).get("feeds", [])

    daily_input_error = ""
    daily_input: dict[str, Any] = {}
    try:
        daily_input = load_daily_input(config)
    except Exception as exc:
        daily_input_error = str(exc)

    try:
        runtime_cfg = load_openclaw_runtime_config()
    except Exception:
        runtime_cfg = {}
    memory_status = backend_status(config)
    graph_status = graph_backend_status(config)
    telegram_runtime = (runtime_cfg.get("channels") or {}).get("telegram") or {}
    telegram_enabled = bool(telegram_runtime.get("enabled"))
    telegram_target = config.telegram_target or (
        "telegram-runtime-enabled" if telegram_enabled else ""
    )
    ready_briefs = [
        item
        for item in (daily_brief_queue.get("briefs") or [])
        if item.get("enabled", True)
        and str(item.get("status") or "").strip().lower() in {"ready", "queued", "approved"}
    ]
    overdue_ready_briefs = [item for item in ready_briefs if item.get("overdue")]

    try:
        pil_available = True
    except Exception:
        pil_available = False

    checks = {
        "workspace_bootstrapped": config.data_dir.exists()
        and config.runs_dir.exists()
        and config.logs_dir.exists(),
        "daily_input_file_exists": daily_input_exists,
        "daily_brief_queue_exists": config.daily_brief_queue_file.exists(),
        "prompt_file_exists": prompt_file_exists,
        "prompt_available": bool(daily_input.get("prompt")) or prompt_file_exists,
        "prompt_actionable": prompt_is_actionable(daily_input.get("prompt", "")),
        "daily_input_structured_complete": bool(daily_input.get("structured_complete", False)),
        "daily_brief_queue_ready": bool(ready_briefs),
        "competitor_url_configured": bool(daily_input.get("competitor_url")),
        "feeds_configured": bool(feeds),
        "approval_required": bool(config.approval_required),
        "browser_profiles_named": all(
            [
                config.browser_profile_linkedin,
                config.browser_profile_facebook,
                config.browser_profile_x,
            ]
        ),
        "langgraph_available": bool(globals().get("langgraph_supported", False)),
        "graph_store_available": graph_status["available"],
        "pil_available": pil_available,
        "openclaw_binary_found": bool(shutil.which(config.openclaw_bin)),
    }

    # Â§27.10: SLA Compliance Check
    recent_runs = store.list_runs(limit=5)
    sla_violations = 0
    total_runs_checked = 0
    total_target = config.latency_targets.get("total", 120000)
    for run in recent_runs:
        latency = (run.get("analysis") or {}).get("latency_ms") or {}
        total_lat = latency.get("total", 0)
        if total_lat > 0:
            total_runs_checked += 1
            if total_lat > total_target:
                sla_violations += 1

    checks["sla_compliant"] = (sla_violations == 0) if total_runs_checked > 0 else True
    checks["sla_avg_latency_ok"] = True # Placeholder for more complex logic

    ready_for_supervised_run = all(
        [
            checks["workspace_bootstrapped"],
            checks["prompt_available"],
            checks["prompt_actionable"],
            checks["competitor_url_configured"],
            checks["feeds_configured"],
        ]
    )

    image_backend = (
        "procedural_local" if config.image_backend == "procedural" else config.image_backend
    )
    return cast(
        dict[str, Any],
        {
            "status": "ok" if ready_for_supervised_run else "attention",
            "ready_for_supervised_run": ready_for_supervised_run,
            "automation_paused": control.get("automation_paused", False),
            "preferred_model": config.preferred_model or "set-in-openclaw",
            "confidence_threshold": config.confidence_threshold,
            "auto_publish_enabled": config.auto_publish_enabled,
            "daily_input": {
                "path": str(config.daily_input_file),
                "exists": daily_input_exists,
                "error": daily_input_error,
                "resolved": daily_input,
            },
            "daily_brief_queue": {
                "path": str(config.daily_brief_queue_file),
                "exists": config.daily_brief_queue_file.exists(),
                "ready_count": len(ready_briefs),
                "overdue_ready_count": len(overdue_ready_briefs),
                "briefs": ready_briefs[:10],
            },
            "feeds": {
                "path": str(config.feeds_file),
                "count": len(feeds),
            },
            "research_backends": {
                "rss": True,
                "public_news": bool(config.enable_public_news),
                "searxng": bool(config.searx_url),
                "pytrends": bool(config.enable_pytrends),
            },
            "memory": memory_status,
            "graph": graph_status,
            "telegram": {
                "target": telegram_target,
                "configured_in_workspace": bool(config.telegram_target or telegram_enabled),
            },
            "image_backend": image_backend,
            "memory_backend": {
                "selected": config.memory_backend,
            },
            "graph_backend": {
                "selected": config.graph_backend,
            },
            "browser_profiles": {
                "linkedin": config.browser_profile_linkedin,
                "facebook": config.browser_profile_facebook,
                "x": config.browser_profile_x,
            },
            "publish_urls": {
                "linkedin": config.publish_url_linkedin,
                "facebook": config.publish_url_facebook,
                "x": config.publish_url_x,
            },
            "checks": checks,
        },
    )


def compute_confidence(run: dict[str, Any]) -> float:
    article = run.get("article") or {}
    trends = run.get("trend_signals") or []
    quality_gate = _quality_gate_payload(run)
    metrics = quality_gate.get("metrics") or {}
    base = 0.15
    if article.get("clean_text"):
        base += 0.2
    if article.get("title"):
        base += 0.1
    if len(trends) >= 3:
        base += 0.15
    if run.get("keywords"):
        base += 0.1
    if run.get("platform_targets"):
        base += 0.1
    if run.get("memory_hits"):
        base += 0.1
    if run.get("graph_hits"):
        base += 0.05
    quality_gate_ready = any(
        key in quality_gate
        for key in ("score", "overall_pass", "checks", "metrics", "publish_blockers", "remediation")
    )
    if not quality_gate_ready:
        return round(min(base, 0.85), 2)

    score = (min(base, 0.85) * 0.35) + (float(quality_gate.get("score", 0.0)) * 0.35)
    if quality_gate.get("overall_pass"):
        score += 0.05
    competitor_overlap = float(metrics.get("competitor_overlap_ratio", 0.0) or 0.0)
    historical_overlap = float(metrics.get("historical_overlap_ratio", 0.0) or 0.0)
    score += max(0.0, 0.07 - min(0.07, competitor_overlap * 0.08))
    score += max(0.0, 0.04 - min(0.04, historical_overlap * 0.05))
    if (metrics.get("source_safety_severity") or "low") == "low":
        score += 0.03
    else:
        score -= 0.06
    trend_titles_available = int(metrics.get("trend_titles_available", 0) or 0)
    trend_titles_used = int(metrics.get("trend_titles_used", 0) or 0)
    if trend_titles_available > 0 and trend_titles_used > 0:
        score += 0.05
    elif trend_titles_available == 0:
        score -= 0.05
    blockers = list(quality_gate.get("publish_blockers") or [])
    score -= min(0.16, len(blockers) * 0.04)
    return round(max(0.0, min(score, 0.97)), 2)


def render_source_summary(article: CompetitorArticle) -> str:
    headings = "\n".join(f"- {item}" for item in article.headings[:8]) or "- None captured"
    links = "\n".join(f"- {item}" for item in article.links[:10]) or "- None captured"
    return (
        f"# Competitor Source Summary\n\n"
        f"URL: {article.url}\n\n"
        f"Title: {article.title}\n\n"
        f"Headings:\n{headings}\n\n"
        f"Links:\n{links}\n\n"
        f"Summary:\n\n{article.summary}\n"
    )


def render_trend_summary(run: dict[str, Any]) -> str:
    signals = run.get("trend_signals") or []
    trend_research = (run.get("analysis") or {}).get("trend_research") or {}
    lines = [
        "# Trend Summary",
        "",
        f"Keywords: {', '.join(run.get('keywords') or [])}",
        f"Context terms: {', '.join(trend_research.get('context_terms') or []) or 'None'}",
        f"Raw signals: {trend_research.get('raw_signal_count', 0)}",
        f"Curated signals: {trend_research.get('curated_signal_count', 0)}",
        f"Discarded signals: {trend_research.get('discarded_signal_count', 0)}",
        "Sources: "
        + ", ".join(
            f"{source}={count}"
            for source, count in sorted((trend_research.get("sources") or {}).items())
        ),
        "",
    ]
    for signal in signals[:12]:
        lines.append(
            f"- [{signal.get('title', 'Untitled')}]({signal.get('url', '')}) | source={signal.get('source', '')} | score={signal.get('score', 0)}"
        )
    return "\n".join(lines) + "\n"


def render_memory_context(run: dict[str, Any]) -> str:
    hits = run.get("memory_hits") or []
    lines = ["# Memory Context", ""]
    if not hits:
        lines.append("- No historical run memory was retrieved.")
    else:
        for item in hits:
            lines.append(
                f"- {humanize_memory_hit(item)} | kind={item.get('kind', '')} | score={item.get('score', 0)}"
            )
    lines.append("")
    lines.append("Original angle:")
    lines.append("")
    lines.append((run.get("analysis") or {}).get("original_angle", "Not generated yet."))
    return "\n".join(lines) + "\n"


def render_graph_context(run: dict[str, Any]) -> str:
    hits = run.get("graph_hits") or []
    lines = ["# Knowledge Graph Context", ""]
    if not hits:
        lines.append("- No graph relationships were retrieved.")
    else:
        for item in hits[:6]:
            connections = item.get("connections") or []
            relation_text = (
                "; ".join(
                    f"{edge.get('relation', 'RELATED')} -> {edge.get('peerLabel') or edge.get('peer', '')}"
                    for edge in connections[:3]
                )
                or "no linked peers"
            )
            lines.append(
                f"- {item.get('label', item.get('id', 'node'))} | kind={item.get('kind', '')} "
                f"| score={item.get('score', 0)} | {relation_text}"
            )
    lines.append("")
    lines.append("Knowledge graph brief:")
    lines.append("")
    lines.append((run.get("analysis") or {}).get("knowledge_graph_brief", "Not generated yet."))
    return "\n".join(lines) + "\n"


def render_zero_cost_proof(run: dict[str, Any]) -> str:
    cost_proof = run.get("cost_proof") or {}
    return json.dumps(
        {
            "run_id": run.get("run_id", ""),
            "paid_services_used": [],
            "free_backends_used": cost_proof.get("free_backends_used", []),
            "image_backend": cost_proof.get("image_backend", ""),
            "llm_surface": cost_proof.get("llm_surface", ""),
        },
        indent=2,
        ensure_ascii=True,
    )


def build_generation_brief(run: dict[str, Any]) -> str:
    article = run.get("article") or {}
    analysis = run.get("analysis") or {}
    daily_input = run.get("daily_input") or {}
    quality_gate = run.get("quality_gate") or {}
    metrics = quality_gate.get("metrics") or {}
    source_safety = analysis.get("source_safety") or {}
    trend_research = analysis.get("trend_research") or {}
    diversity = analysis.get("portfolio_diversity") or {}
    brand_summary = summarize_brand_profile(analysis.get("brand_profile", ""))
    trend_lines = (
        "\n".join(
            f"- {item.get('title', 'Untitled')} ({item.get('source', '')})"
            for item in (run.get("trend_signals") or [])[:8]
        )
        or "- No trend signals captured."
    )
    memory_lines = (
        "\n".join(
            f"- {humanize_memory_hit(item)} ({item.get('kind', '')})"
            for item in (run.get("memory_hits") or [])[:5]
        )
        or "- No historical memory hits."
    )
    graph_lines = (
        "\n".join(
            f"- {item.get('label', item.get('id', 'node'))} ({item.get('kind', '')})"
            for item in (run.get("graph_hits") or [])[:5]
        )
        or "- No knowledge graph hits."
    )
    return f"""# OpenClaw Generation Brief

Run ID: {run["run_id"]}
Prompt: {run["prompt"]}
Competitor URL: {run["competitor_url"]}
Keywords: {", ".join(run.get("keywords") or [])}
Confidence: {run.get("confidence")}
Graph mode: {run.get("graph_mode", "sequential")}

## Daily Brief

- Prompt source: {daily_input.get("prompt_source", "direct")}
- Topic: {daily_input.get("topic", "") or "Not specified"}
- Business angle: {daily_input.get("business_angle", "") or "Not specified"}
- Target audience: {daily_input.get("target_audience", "") or "Not specified"}
- Key CTA: {daily_input.get("key_call_to_action", "") or "Not specified"}
- Do not say: {", ".join(daily_input.get("do_not_say") or []) or "None"}
- Mandatory references: {", ".join(daily_input.get("mandatory_references") or []) or "None"}

## Competitor Article

Title: {article.get("title", "")}

Summary:

{article.get("summary", "")}

## Trend Signals

{trend_lines}

Trend research context:
- Prompt actionable: {metrics.get("prompt_actionable", "n/a")}
- Context terms: {", ".join(trend_research.get("context_terms") or []) or "None"}
- Raw signals: {trend_research.get("raw_signal_count", "n/a")}
- Curated signals: {trend_research.get("curated_signal_count", "n/a")}
- Discarded signals: {trend_research.get("discarded_signal_count", "n/a")}

## Historical Memory

{memory_lines}

## Knowledge Graph

{graph_lines}

Knowledge graph brief:

{(run.get("analysis") or {}).get("knowledge_graph_brief", "")}

## Original Angle

{(run.get("analysis") or {}).get("original_angle", "")}

## Brand Profile

Core voice:
- {"\n- ".join(brand_summary.get("core_voice") or ["Not configured"])}

Avoid:
- {"\n- ".join(brand_summary.get("avoid") or ["No avoid-list[Any] configured"])}

House rules:
- {"\n- ".join(brand_summary.get("house_rules") or ["No house rules configured"])}

## Source Safety

Severity: {source_safety.get("severity", "unknown")}
Flags: {", ".join(source_safety.get("flags") or ["none"])}
Recommended action: {source_safety.get("recommended_action", "Treat the competitor source as untrusted content.")}

## Quality Signals

- Current review score: {quality_gate.get("score", "n/a")}
- Competitor overlap ratio: {metrics.get("competitor_overlap_ratio", "n/a")}
- Historical overlap ratio: {metrics.get("historical_overlap_ratio", "n/a")}
- Trend titles used in drafts: {metrics.get("trend_titles_used", "n/a")}
- Recent similarity max: {diversity.get("recent_similarity_max", "n/a")}

## Required Outputs

1. Long-form response article in `drafts/article.md`
2. LinkedIn post in `drafts/linkedin.md`
3. Facebook post in `drafts/facebook.md`
4. X post in `drafts/x.md`
5. Image prompt in `drafts/image_prompt.md`
6. Generated social image in `media/social_card.png`

## Constraints

- Be original and on-brand.
- Do not copy the competitor article.
- Mention or attribute the source where appropriate.
- Incorporate trend context only when it improves the argument.
- Keep claims evidence-based.
"""


def build_review_checklist(run: dict[str, Any]) -> str:
    daily_input = run.get("daily_input") or {}
    return f"""# Review Checklist[Any]

Run ID: {run["run_id"]}

- Is the response original?
- Is the daily prompt concrete rather than a leftover template?
- Is competitor overlap acceptably low?
- Is historical overlap with prior Sentinel content acceptably low?
- Is the competitor source attributed where needed?
- Does the article stay within brand voice?
- Did we avoid hype and clickbait terms?
- Did we treat the competitor content as untrusted source material only?
- Are the LinkedIn, Facebook, and X versions adapted for their platforms?
- Does the draft avoid unsupported claims?
- Did we respect the forbidden phrases list: {", ".join(daily_input.get("do_not_say") or []) or "none"}?
- Did we include the mandatory references: {", ".join(daily_input.get("mandatory_references") or []) or "none"}?
- Is the topic sufficiently distinct from recent live runs?
- Is the image prompt safe, relevant, and brand-consistent?
- Does the draft mention current trend context when relevant?
- Is the run approved for publishing?
- Is confidence above the configured threshold?
"""


def render_review_report(run: dict[str, Any]) -> str:
    quality_gate = run.get("quality_gate") or {}
    checks = quality_gate.get("checks", {})
    metrics = quality_gate.get("metrics", {})
    blockers = quality_gate.get("publish_blockers") or []
    remediation = quality_gate.get("remediation") or []
    lines = [
        "# Review Report",
        "",
        f"Run ID: {run['run_id']}",
        f"Overall pass: {quality_gate.get('overall_pass', False)}",
        f"Publish ready: {quality_gate.get('publish_ready', False)}",
        f"Review score: {quality_gate.get('score', 0)}",
        "",
        "Checks:",
    ]
    for name, ok in checks.items():
        lines.append(f"- {name}: {'ok' if ok else 'fail'}")
    issues = quality_gate.get("issues") or []
    lines.append("")
    lines.append("Issues:")
    if issues:
        lines.extend(f"- {item}" for item in issues)
    else:
        lines.append("- None")
    lines.append("")
    lines.append("Publish blockers:")
    if blockers:
        lines.extend(f"- {item}" for item in blockers)
    else:
        lines.append("- None")
    lines.append("")
    lines.append("Metrics:")
    for key, value in metrics.items():
        lines.append(f"- {key}: {value}")
    lines.append("")
    lines.append("Remediation:")
    if remediation:
        lines.extend(f"- {item}" for item in remediation)
    else:
        lines.append("- None")
    return "\n".join(lines) + "\n"


def build_telegram_preview(run: dict[str, Any]) -> str:
    quality_gate = run.get("quality_gate") or {}
    issues = quality_gate.get("issues") or []
    metrics = quality_gate.get("metrics") or {}
    trend_research = (run.get("analysis") or {}).get("trend_research") or {}
    diversity = (run.get("analysis") or {}).get("portfolio_diversity") or {}
    daily_input = run.get("daily_input") or {}
    pipeline = (run.get("analysis") or {}).get("delivery_pipeline") or {}
    if not pipeline:
        pipeline = {
            "current_step": "n/a",
            "next_action": "n/a",
            "steps": [
                {"step": 1, "title": "Ingest daily brief + competitor article", "status": "pending"},
                {"step": 2, "title": "Research public trends at zero cost", "status": "pending"},
                {"step": 3, "title": "Analyze + generate article and social drafts", "status": "pending"},
                {"step": 4, "title": "Generate the visual asset", "status": "pending"},
                {"step": 5, "title": "Telegram monitoring, approval, and overrides", "status": "pending"},
                {"step": 6, "title": "Automatic browser posting to LinkedIn, Facebook, and X", "status": "pending"},
            ],
        }
    step_lines = []
    for item in pipeline.get("steps") or []:
        step_lines.append(
            f"{item.get('step', '?')}. {item.get('title', '')}: {item.get('status', 'unknown')}"
        )
    return f"""Run: {run["run_id"]}
Status: {run["status"]}
Approval: {run.get("approval_state")}
Confidence: {run.get("confidence")}
Publish ready: {quality_gate.get("publish_ready", False)}
Review score: {quality_gate.get("score", "n/a")}
Prompt actionable: {metrics.get("prompt_actionable", "n/a")}
Prompt source: {daily_input.get("prompt_source", "direct")}
Topic: {daily_input.get("topic", "") or "n/a"}
Source safety: {metrics.get("source_safety_severity", "n/a")}
Competitor overlap: {metrics.get("competitor_overlap_ratio", "n/a")}
History overlap: {metrics.get("historical_overlap_ratio", "n/a")}
Recent similarity max: {diversity.get("recent_similarity_max", "n/a")}
Curated trend signals: {trend_research.get("curated_signal_count", 0)}/{trend_research.get("raw_signal_count", 0)}
Targets: {", ".join(run.get("platform_targets") or [])}
Keywords: {", ".join(run.get("keywords") or [])}
Telegram route: {run.get("telegram_route") or "not set"}
Current step: {pipeline.get("current_step", "n/a")}/6
Next action: {pipeline.get("next_action", "n/a")}

Top issues:
{chr(10).join(f"- {item}" for item in issues[:5]) if issues else "- None"}

6-step agent flow:
{chr(10).join(step_lines) if step_lines else "- Pipeline not built yet"}

Artifacts:
- prompts/openclaw_generation_brief.md
- prompts/openclaw_review_checklist.md
- ops/review_report.md
- artifacts/source_safety.md
- drafts/article.md
- drafts/linkedin.md
- drafts/facebook.md
- drafts/x.md
- drafts/image_prompt.md
- media/social_card.png

Operator commands:
- /doctor
- /approve {run["run_id"]}    (approves then launches automatic posting)
- /reject {run["run_id"]}
- /logs {run["run_id"]}
- /publish {run["run_id"]}
- /stage-publish {run["run_id"]}
"""


def build_posting_checklist(run: dict[str, Any]) -> str:
    targets = "\n".join(f"- {platform}" for platform in run.get("platform_targets") or [])
    daily_input = run.get("daily_input") or {}
    return f"""# Posting Checklist[Any]

Run ID: {run["run_id"]}

Targets:
{targets}

Before each publish:

- Verify confidence is above the configured threshold.
- Verify the review report says `publish_ready: true`.
- Verify forbidden phrases are absent: {", ".join(daily_input.get("do_not_say") or []) or "none"}.
- Verify mandatory references are present: {", ".join(daily_input.get("mandatory_references") or []) or "none"}.
- Open the dedicated browser profile for the platform.
- Verify you are logged into the correct account.
- Run `python -m openclaw_content_sentinel.cli browser-publish --run-id {run["run_id"]} --platform <platform>` for live browser publishing.
- Or run `python -m openclaw_content_sentinel.cli publish --run-id {run["run_id"]} --platform <platform>` to stage metadata only.
- Load the platform-specific draft from the run folder.
- Capture a screenshot before submit.
- Submit once.
- Capture the confirmation or resulting post URL.
- Record the outcome with `python -m openclaw_content_sentinel.cli record-post ...`.
"""


def render_status(
    runs: list[dict[str, Any]], control: dict[str, Any], run_id: str = ""
) -> dict[str, Any]:
    if run_id:
        selected = next((run for run in runs if run.get("run_id") == run_id), None)
        pipeline = ((selected or {}).get("analysis") or {}).get("delivery_pipeline", {})
        if selected and not pipeline:
            pipeline = {"current_step": 0, "next_action": "n/a", "steps": []}
        return {
            "automation_paused": control.get("automation_paused", False),
            "pause_reason": control.get("reason", ""),
            "run": selected,
            "pipeline": pipeline,
        }
    latest = runs[:8]
    return {
        "automation_paused": control.get("automation_paused", False),
        "pause_reason": control.get("reason", ""),
        "count": len(runs),
        "latest": [
            {
                "run_id": run.get("run_id"),
                "status": run.get("status"),
                "approval_state": run.get("approval_state"),
                "confidence": run.get("confidence"),
                "graph_mode": run.get("graph_mode", "sequential"),
                "updated_at": run.get("updated_at"),
            }
            for run in latest
        ],
    }


def render_logs(run: dict[str, Any], artifacts: list[str]) -> dict[str, Any]:
    return {
        "run_id": run["run_id"],
        "status": run["status"],
        "approval_state": run.get("approval_state"),
        "schema_version": run.get("schema_version"),
        "publish_adapter_version": run.get("publish_adapter_version"),
        "prompt_bundle_version": run.get("prompt_bundle_version"),
        "retrieval_policy_version": run.get("retrieval_policy_version"),
        "scoring_policy_version": run.get("scoring_policy_version"),
        "approval_policy": run.get("approval_policy"),
        "trigger_kind": run.get("trigger_kind"),
        "confidence": run.get("confidence"),
        "daily_input": run.get("daily_input", {}),
        "quality_gate": run.get("quality_gate", {}),
        "identity": run.get("identity", {}),
        "proof_pack": run.get("proof_pack", {}),
        "analysis": {
            "hybrid_context": (run.get("analysis") or {}).get("hybrid_context", {}),
            "evidence_pack": (run.get("analysis") or {}).get("evidence_pack", {}),
            "source_registry": (run.get("analysis") or {}).get("source_registry", {}),
            "publish_strategy": (run.get("analysis") or {}).get("publish_strategy", {}),
            "recovery_plan": (run.get("analysis") or {}).get("recovery_plan", {}),
            "publication_timing": (run.get("analysis") or {}).get("publication_timing", {}),
            "approval_packet": (run.get("analysis") or {}).get("approval_packet", {}),
            "execution_policy": (run.get("analysis") or {}).get("execution_policy", {}),
            "delivery_pipeline": (run.get("analysis") or {}).get("delivery_pipeline", {}),
        },
        "graph_mode": run.get("graph_mode"),
        "telegram_route": run.get("telegram_route"),
        "telegram_preview_sent_at": run.get("telegram_preview_sent_at"),
        "stage_timestamps": run.get("stage_timestamps", {}),
        "cost_proof": run.get("cost_proof", {}),
        "outputs": run.get("outputs", {}),
        "post_results": run.get("post_results", {}),
        "artifacts": artifacts,
        "notes": run.get("notes", []),
        "simulation": run.get("simulation", {}),
    }


def browser_profile_for(config: AppConfig, platform: str) -> str:
    mapping = {
        "linkedin": config.browser_profile_linkedin,
        "facebook": config.browser_profile_facebook,
        "x": config.browser_profile_x,
    }
    return mapping.get(platform, "")


def compose_url_for(config: AppConfig, platform: str) -> str:
    mapping = {
        "linkedin": config.publish_url_linkedin,
        "facebook": config.publish_url_facebook,
        "x": config.publish_url_x,
    }
    return mapping.get(platform, "")


def find_first_existing_image(run_dir: Path) -> Path | None:
    for pattern in ("*.png", "*.jpg", "*.jpeg", "*.webp"):
        matches = sorted(run_dir.rglob(pattern))
        if matches:
            return matches[0]
    return None


def recompute_run_status(run: dict[str, Any]) -> str:
    targets = run.get("platform_targets") or []
    results = run.get("post_results") or {}
    statuses = [results.get(target, {}).get("status", "pending") for target in targets]

    if "failed" in statuses:
        return "posting_failed"
    if targets and all(status in {"posted", "skipped"} for status in statuses):
        return "posted"
    if any(status not in {"pending"} for status in statuses):
        return "posting"
    if run.get("approval_state") == "approved":
        return "approved"
    if run.get("approval_state") == "rejected":
        return "rejected"
    return "awaiting_approval"

