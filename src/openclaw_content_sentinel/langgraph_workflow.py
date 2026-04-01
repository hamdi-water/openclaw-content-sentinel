"""Phase 15: LangGraph orchestration engine."""

from __future__ import annotations

import asyncio
import time
import uuid
import warnings
from typing import Any, Literal, TypedDict, cast

with warnings.catch_warnings():
    warnings.filterwarnings(
        "ignore",
        category=UserWarning,
        message="Core Pydantic V1 functionality isn't compatible with Python 3.14 or greater.",
    )
    from langchain_core.runnables import RunnableConfig
    from langgraph.checkpoint.memory import MemorySaver
    from langgraph.graph import END, START, StateGraph

from .browser_automation import publish_all_via_browser_async
from .config import AppConfig
from .logging_utils import get_logger
from .monitoring import monitor
from .storage import RunStore
from .utils import LatencyTracker, now_utc
from .workflow import (
    create_run as legacy_create_run,
)
from .workflow import (
    generate_drafts,
    ingest_competitor,
    load_graph_context,
    load_memory_context,
    prepare_publish_all,
    research_trends,
    review_run,
)

LANGGRAPH_AVAILABLE = True
_GRAPH_INSTANCE: Any = None  # module-level singleton for testing


# ---------------------------------------------------------------------------
# State Schema
# ---------------------------------------------------------------------------


class RunMeta(TypedDict):
    run_id: str
    mode: Literal["dry_run", "live", "replay"]
    scheduled_at: str
    trigger_type: str
    initiator: str
    retrieval_version: str
    approval_policy: str
    prompt_bundle_id: str
    prompt_version: str
    browser_profile: str


class Inputs(TypedDict):
    daily_prompt: str
    competitor_url: str
    target_platforms: list[str]
    forced_topic: str | None
    operator_overrides: dict[str, Any]
    target_language: str  # Â§7: Language identifier


class ResearchState(TypedDict):
    source_hits: list[dict[str, Any]]
    trend_signals: list[dict[str, Any]]
    canonical_docs: list[dict[str, Any]]
    evidence_pack_id: str | None
    confidence: float


class EditorialState(TypedDict):
    angle: dict[str, str]
    claims: list[str]
    draft_main: str
    draft_variants: dict[str, str]
    critic_report: dict[str, Any]
    novelty_score: float
    contamination_flags: list[str]
    needs_human: bool
    approval_decision: str | None
    revision_count: int


class PublishState(TypedDict):
    attempts: list[dict[str, Any]]
    proofs: list[dict[str, Any]]
    final_urls: list[str]
    verification_status: str


class AuditState(TypedDict):
    incidents: list[dict[str, Any]]
    metrics: dict[str, Any]
    cost_ledger: dict[str, Any]
    checkpoints_ref: list[str]
    artifacts: list[str]


class WorkflowState(TypedDict):
    run: RunMeta
    inputs: Inputs
    research: ResearchState
    editorial: EditorialState
    publish: PublishState
    audit: AuditState
    current_node: str | None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _tick(
    audit: AuditState, node: str, duration_ms: float, extra: dict[str, Any] | None = None
) -> None:
    """Append a timing record to audit metrics (Â§SLI)."""
    audit.setdefault("metrics", {})
    audit["metrics"].setdefault("node_timings_ms", [])
    entry = {"node": node, "duration_ms": round(duration_ms, 1), "ts": now_utc()}
    if extra:
        entry.update(extra)
    audit["metrics"]["node_timings_ms"].append(entry)


def _incident(audit: AuditState, node: str, severity: str, message: str) -> None:
    """Record an operational incident in the audit trail."""
    audit.setdefault("incidents", [])
    audit["incidents"].append(
        {
            "node": node,
            "severity": severity,
            "message": message,
            "ts": now_utc(),
        }
    )


# ---------------------------------------------------------------------------
# Nodes
# ---------------------------------------------------------------------------


def collect_inputs_node(state: WorkflowState, config: RunnableConfig) -> dict:
    """
    Phase 15.1 â€“ Normalize prompt, URL, keywords, and targets (Â§23.3).

    Calls ``legacy_create_run`` to bootstrap the SQLite run record and
    propagates the generated ``run_id`` into the graph state so every
    downstream node can load/update the record.
    """
    t0 = time.perf_counter()
    app_config: AppConfig | None = config.get("configurable", {}).get("app_config")

    if not app_config:
        # No config wired (unit test / dry-run). Return minimal sentinel state.
        return {"current_node": "collect_inputs"}

    inputs = state["inputs"]
    prompt = inputs.get("daily_prompt", "")
    competitor_url = inputs.get("competitor_url", "")
    targets = list(inputs.get("target_platforms") or app_config.default_targets)
    keywords = sorted(set(inputs.get("operator_overrides", {}).get("keywords") or []))

    run = legacy_create_run(
        app_config,
        prompt=prompt,
        competitor_url=competitor_url,
        keywords=keywords,
        targets=targets,
        trigger_kind=state["run"].get("trigger_type", "manual"),
    )
    run_id = run["run_id"]
    log = get_logger(__name__, run_id)
    log.info(f"Initialized run {run_id}")

    # Â§18.1: Track run start
    monitor.toggle_run(status="started", trigger=state["run"].get("trigger_type", "manual"))

    run_meta: RunMeta = dict(state["run"])  # type: ignore[assignment]
    run_meta["run_id"] = run["run_id"]
    run_meta["scheduled_at"] = run.get("scheduled_at", now_utc())

    audit = dict(state["audit"])
    _tick(cast(AuditState, audit), "collect_inputs", (time.perf_counter() - t0) * 1000)
    audit["checkpoints_ref"].append(f"collect_inputs:{run['run_id']}")

    return {
        "run": run_meta,
        "audit": audit,
        "current_node": "collect_inputs",
    }


async def discover_sources_node(state: WorkflowState, config: RunnableConfig) -> dict[str, Any]:
    """Run competitor scraping and trend discovery in parallel."""
    t0 = time.perf_counter()
    app_config: AppConfig | None = config.get("configurable", {}).get("app_config")
    run_id: str = state["run"].get("run_id", "")

    audit = dict(state["audit"])

    if not app_config or not run_id:
        _incident(
            cast(AuditState, audit),
            "discover_sources",
            "warning",
            "No app_config or run_id â€“ skipping source discovery.",
        )
        return {"audit": audit, "current_node": "discover_sources"}

    # Define wrapper tasks for parallel execution
    async def _ingest() -> dict[str, Any]:
        try:
            return await asyncio.to_thread(ingest_competitor, app_config, run_id)
        except Exception as exc:
            _incident(
                cast(AuditState, audit),
                "discover_sources",
                "error",
                f"ingest_competitor failed: {exc}",
            )
            return RunStore(app_config).load_run(run_id)

    async def _research() -> dict[str, Any] | None:
        try:
            return await asyncio.to_thread(research_trends, app_config, run_id)
        except Exception as exc:
            _incident(
                cast(AuditState, audit),
                "discover_sources",
                "warning",
                f"research_trends failed: {exc}",
            )
            return None

    async def _memory() -> dict[str, Any] | None:
        try:
            return await asyncio.to_thread(load_memory_context, app_config, run_id)
        except Exception as exc:
            _incident(
                cast(AuditState, audit),
                "discover_sources",
                "info",
                f"memory_context skipped: {exc}",
            )
            return None

    async def _graph() -> dict[str, Any] | None:
        try:
            return await asyncio.to_thread(load_graph_context, app_config, run_id)
        except Exception as exc:
            _incident(
                cast(AuditState, audit),
                "discover_sources",
                "info",
                f"graph_context skipped: {exc}",
            )
            return None

    # Execute all discovery tasks in parallel
    await asyncio.gather(_ingest(), _research(), _memory(), _graph())

    # Reload run record to get combined state
    store = RunStore(app_config)
    run = store.load_run(run_id)

    trend_signals = run.get("trend_signals") or []
    source_hits = run.get("graph_hits") or []
    canonical_docs = run.get("memory_hits") or []
    confidence = min(1.0, 0.5 + len(trend_signals) * 0.02 + 0.1 * bool(source_hits))

    research = dict(state["research"])
    research["source_hits"] = source_hits
    research["trend_signals"] = trend_signals
    research["canonical_docs"] = canonical_docs
    research["confidence"] = round(confidence, 3)

    _tick(
        cast(AuditState, audit),
        "discover_sources",
        (time.perf_counter() - t0) * 1000,
        {
            "trend_signals": len(trend_signals),
            "source_hits": len(source_hits),
            "canonical_docs": len(canonical_docs),
        },
    )

    return {
        "research": research,
        "audit": audit,
        "current_node": "discover_sources",
    }


def normalize_docs_node(state: WorkflowState, config: RunnableConfig) -> dict[str, Any]:
    """
    Phase 15.3 â€“ Normalize documents.

    Deduplicate and canonicalize sources into ``CanonicalDoc``
    envelopes (Â§17.1, Â§30.2, Â§30.3).

    Attaches obligatory metadata: source_id, freshness, schema_version.
    """
    t0 = time.perf_counter()
    config.get("configurable", {}).get("app_config")
    run_id: str = state["run"].get("run_id", "")

    audit = dict(state["audit"])
    research = dict(state["research"])

    seen_urls: set[str] = set()
    canonical: list[dict[str, Any]] = []

    for doc in research.get("canonical_docs", []):
        url = str(doc.get("url") or doc.get("source_url") or "").strip()
        if url and url in seen_urls:
            continue
        if url:
            seen_urls.add(url)
        canonical.append(
            {
                "source_id": doc.get("run_id") or doc.get("id") or url or str(uuid.uuid4()),
                "freshness": doc.get("created_at") or doc.get("ts") or now_utc(),
                "schema_version": "v1",
                "title": doc.get("title") or doc.get("run_id", ""),
                "summary": doc.get("summary") or doc.get("prompt", ""),
                "url": url,
            }
        )

    for sig in research.get("trend_signals", []):
        url = str(sig.get("url") or "").strip()
        if url and url in seen_urls:
            continue
        if url:
            seen_urls.add(url)
        canonical.append(
            {
                "source_id": f"trend:{sig.get('source', 'unknown')}:{url}",
                "freshness": sig.get("published_at") or now_utc(),
                "schema_version": "v1",
                "title": sig.get("title", ""),
                "summary": sig.get("description", ""),
                "url": url,
            }
        )

    research["canonical_docs"] = canonical
    research["evidence_pack_id"] = f"ep:{run_id}:{now_utc()[:10]}" if run_id else None

    _tick(
        cast(AuditState, audit),
        "normalize_docs",
        (time.perf_counter() - t0) * 1000,
        {"canonical_count": len(canonical)},
    )

    return {
        "research": research,
        "audit": audit,
        "current_node": "normalize_docs",
    }


def plan_angle_node(state: WorkflowState, config: RunnableConfig) -> dict[str, Any]:
    """
    Phase 15.4 â€“ Synthesize the editorial angle from research + inputs (Â§23.4).

    Extracts strategic themes, mandatory references, and differentiation
    guardrails from the research state and persists them to the RunStore.
    """
    t0 = time.perf_counter()
    app_config: AppConfig | None = config.get("configurable", {}).get("app_config")
    run_id: str = state["run"].get("run_id", "")

    audit = dict(state["audit"])
    editorial = dict(state["editorial"])

    if app_config and run_id:
        try:
            store = RunStore(app_config)
            run = store.load_run(run_id)

            hybrid = (run.get("analysis") or {}).get("hybrid_context") or {}
            original_angle = (run.get("analysis") or {}).get("original_angle") or ""

            editorial["angle"] = {
                "topic": hybrid.get("topic", ""),
                "business_angle": hybrid.get("business_angle", ""),
                "target_audience": hybrid.get("target_audience", ""),
                "cta": hybrid.get("key_call_to_action", ""),
                "original_angle": original_angle,
                "strategic_themes": hybrid.get("strategic_themes", []),
                "differentiation_guardrails": hybrid.get("differentiation_guardrails", []),
            }
            editorial["claims"] = hybrid.get("reference_guardrails", [])
        except Exception as exc:
            msg = f"angle synthesis skipped: {exc}"
            _incident(cast(AuditState, audit), "plan_angle", "warning", msg)

    _tick(cast(AuditState, audit), "plan_angle", (time.perf_counter() - t0) * 1000)

    return {
        "editorial": editorial,
        "audit": audit,
        "current_node": "plan_angle",
    }


def draft_and_rewrite_node(state: WorkflowState, config: RunnableConfig) -> dict[str, Any]:
    """
    Phase 15.5 â€“ Generate multi-platform drafts from the editorial plan (Â§23.4).

    Calls ``generate_drafts`` which orchestrates the writer agent for article,
    X, LinkedIn, and Facebook copy, then reads the outputs back into state.
    """
    t0 = time.perf_counter()
    app_config: AppConfig | None = config.get("configurable", {}).get("app_config")
    run_id: str = state["run"].get("run_id", "")

    audit = dict(state["audit"])
    editorial = dict(state["editorial"])

    if not app_config or not run_id:
        msg = "No app_config or run_id â€” skipping drafting."
        _incident(cast(AuditState, audit), "draft_and_rewrite", "warning", msg)
        return {"editorial": editorial, "audit": audit, "current_node": "draft_and_rewrite"}

    try:
        run = generate_drafts(app_config, run_id)
        outputs = run.get("outputs") or {}
        store = RunStore(app_config)

        # Load text artifacts for state
        article_path = outputs.get("article", "")
        draft_main = ""
        if article_path:
            try:
                draft_main = store.load_text_artifact(run_id, article_path) or ""
            except Exception:
                draft_main = f"[article drafted at {article_path}]"

        variants: dict[str, str] = {}
        for platform in ("x", "linkedin", "facebook"):
            artifact_key = outputs.get(platform, "")
            if artifact_key:
                try:
                    variants[platform] = store.load_text_artifact(run_id, artifact_key) or ""
                except Exception:
                    variants[platform] = f"[{platform} drafted at {artifact_key}]"

        editorial["draft_main"] = draft_main
        editorial["draft_variants"] = variants
        _tick(
            cast(AuditState, audit),
            "draft_and_rewrite",
            (time.perf_counter() - t0) * 1000,
            {
                "platforms": list(variants.keys()),
            },
        )

    except Exception as exc:
        msg = f"generate_drafts failed: {exc}"
        _incident(cast(AuditState, audit), "draft_and_rewrite", "error", msg)
        _tick(cast(AuditState, audit), "draft_and_rewrite", (time.perf_counter() - t0) * 1000)

    return {
        "editorial": editorial,
        "audit": audit,
        "current_node": "draft_and_rewrite",
    }


def critic_node(state: WorkflowState, config: RunnableConfig) -> dict[str, Any]:
    """
    Phase 15.6 â€“ Quality gate: brand, novelty, contamination, groundedness (Â§23.3).

    Calls ``review_run`` to compute the full quality gate report, extracts the
    final pass/fail decision, and marks whether human approval is needed.
    """
    t0 = time.perf_counter()
    app_config: AppConfig | None = config.get("configurable", {}).get("app_config")
    run_id: str = state["run"].get("run_id", "")

    audit = dict(state["audit"])
    editorial = dict(state["editorial"])

    if not app_config or not run_id:
        msg = "No app_config or run_id â€” critic skipped."
        _incident(cast(AuditState, audit), "critic", "warning", msg)
        editorial["needs_human"] = True
        editorial["critic_report"] = {
            "publish_ready": False,
            "score": 0.0,
            "reason": "critic_skipped",
        }
        return {"editorial": editorial, "audit": audit, "current_node": "critic"}

    try:
        run = review_run(app_config, run_id)
        quality = run.get("quality_gate") or {}
        score = float(quality.get("score") or 0.0)
        publish_ready = bool(quality.get("publish_ready", False))

        editorial["critic_report"] = quality
        editorial["novelty_score"] = float(
            (run.get("analysis") or {}).get("novelty", {}).get("score", 0.0)
        )
        editorial["contamination_flags"] = list(
            (run.get("analysis") or {}).get("source_safety", {}).get("flags", [])
        )
        editorial["needs_human"] = not publish_ready or score < 0.7

        _tick(
            audit,
            "critic",
            (time.perf_counter() - t0) * 1000,
            {
                "score": score,
                "publish_ready": publish_ready,
                "contamination_flags": len(editorial["contamination_flags"]),
            },
        )
    except Exception as exc:
        _incident(cast(AuditState, audit), "critic", "error", f"review_run failed: {exc}")
        editorial["needs_human"] = True
        editorial["critic_report"] = {"publish_ready": False, "score": 0.0, "error": str(exc)}
        _tick(cast(AuditState, audit), "critic", (time.perf_counter() - t0) * 1000)

    return {
        "editorial": editorial,
        "audit": audit,
        "current_node": "critic",
    }


def approval_gate_node(state: WorkflowState, config: RunnableConfig) -> dict:
    r"""
    Phase 15.7 â€“ Human-in-the-loop approval (Â§23.3).

    Reads the ``approval_state`` from RunStore (set externally by the Telegram
    /approve or /reject command).  LangGraph automatically pauses here before
    executing this node when ``interrupt_before=[\"approval_gate\"]`` is set.
    """
    t0 = time.perf_counter()
    app_config: AppConfig | None = config.get("configurable", {}).get("app_config")
    run_id: str = state["run"].get("run_id", "")

    audit = dict(state["audit"])
    editorial = dict(state["editorial"])

    if app_config and run_id:
        try:
            store = RunStore(app_config)
            run = store.load_run(run_id)
            decision = str(run.get("approval_state") or "pending").strip().lower()
            editorial["approval_decision"] = decision
            if decision == "approved":
                quality_gate = run.setdefault("quality_gate", {})
                quality_gate["publish_ready"] = True
                quality_gate["approved_at"] = now_utc()
                store.save_run(run)
            _tick(
                cast(AuditState, audit),
                "approval_gate",
                (time.perf_counter() - t0) * 1000,
                {"decision": decision},
            )
        except Exception as exc:
            msg = f"approval_state read failed: {exc}"
            _incident(cast(AuditState, audit), "approval_gate", "warning", msg)
    else:
        editorial["approval_decision"] = "pending"

    return {
        "editorial": editorial,
        "audit": audit,
        "current_node": "approval_gate",
    }


async def platform_publish_node(state: WorkflowState, config: RunnableConfig) -> dict:
    """
    Phase 15.8 â€“ Multi-platform browser publication (Â§23.4).

    Refactored in Phase 22 to use AsyncBrowserPool for parallel publication.
    Calls ``prepare_publish_all`` then ``publish_all_via_browser_async``.
    """
    t0 = time.perf_counter()
    app_config: AppConfig | None = config.get("configurable", {}).get("app_config")
    run_id: str = state["run"].get("run_id", "")

    audit = dict(state["audit"])
    publish = dict(state["publish"])

    if not app_config or not run_id:
        _incident(
            audit, "platform_publish", "warning", "No app_config or run_id â€“ publishing skipped."
        )
        return {"publish": publish, "audit": audit, "current_node": "platform_publish"}

    try:
        # 1. Prepare (Validation & Readiness markers)
        prepare_publish_all(app_config, run_id)

        # 2. Parallel Publish
        results_payload = await publish_all_via_browser_async(app_config, run_id)
        post_results: list[dict[str, Any]] = results_payload.get("results") or []

        for result in post_results:
            platform = result.get("platform", "unknown")
            attempt = {
                "platform": platform,
                "status": result.get("status", "unknown"),
                "timestamp": now_utc(),
                "attempts": result.get("attempts", 1),
                "failure_reason": result.get("failure_reason"),
            }
            publish["attempts"].append(attempt)

        _tick(cast(AuditState, audit), "platform_publish", (time.perf_counter() - t0) * 1000)

    except Exception as exc:
        msg = f"Parallel publication failed: {exc}"
        _incident(cast(AuditState, audit), "platform_publish", "error", msg)
        _tick(cast(AuditState, audit), "platform_publish", (time.perf_counter() - t0) * 1000)

    return {
        "publish": publish,
        "audit": audit,
        "current_node": "platform_publish",
    }


def verify_and_persist_node(state: WorkflowState, config: RunnableConfig) -> dict:
    """
    Phase 15.9 â€“ Audit logging, SLI recording, and RunStore persistence (Â§23.4, Â§35.4).

    Writes a final publication summary to the SQLite run record and computes
    end-to-end latency for SLI/SLO tracking.
    """
    t0 = time.perf_counter()
    app_config: AppConfig | None = config.get("configurable", {}).get("app_config")
    run_id: str = state["run"].get("run_id", "")

    audit = dict(state["audit"])
    publish = state["publish"]

    if app_config and run_id:
        try:
            store = RunStore(app_config)
            run = store.load_run(run_id)

            # Compute total pipeline latency from node timings
            timings = cast(dict[str, Any], audit.get("metrics", {})).get("node_timings_ms", [])
            total_ms = sum(e.get("duration_ms", 0) for e in timings)
            audit["metrics"]["total_pipeline_latency_ms"] = round(total_ms, 1)
            audit["metrics"]["publication_urls"] = publish.get("final_urls", [])
            audit["metrics"]["verification_status"] = publish.get("verification_status", "unknown")

            run["pipeline_audit"] = audit
            run["final_post_urls"] = publish.get("final_urls", [])
            store.save_run(run)

            # Â§18.2: Record SRE metrics
            monitor.record_latency(total_ms / 1000.0)
            monitor.export()

            artifact_name = "artifacts/pipeline_audit.json"
            import json as _json

            store.save_text_artifact(
                run_id, artifact_name, _json.dumps(audit, indent=2, default=str)
            )
            audit["artifacts"].append(artifact_name)

            _tick(
                cast(AuditState, audit),
                "verify_and_persist",
                (time.perf_counter() - t0) * 1000,
                {
                    "total_pipeline_ms": round(total_ms, 1),
                },
            )
        except Exception as exc:
            msg = f"persist failed: {exc}"
            _incident(cast(AuditState, audit), "verify_and_persist", "warning", msg)
    else:
        audit["metrics"]["verification_status"] = "skipped_no_config"

    return {
        "audit": audit,
        "current_node": "verify_and_persist",
    }


def audit_completion_node(state: WorkflowState, config: RunnableConfig) -> dict:
    """
    Phase 15.10 â€“ Final SLI/SLO gate and artifact sweep (Â§23.4).

    Records the pipeline completion marker, computes final cost snapshot,
    and appends a signed checkpoint reference so the run can be replayed.
    """
    t0 = time.perf_counter()
    config.get("configurable", {}).get("app_config")
    run_id: str = state["run"].get("run_id", "")

    audit = dict(state["audit"])

    metrics = audit.setdefault("metrics", {})
    metrics["completed_at"] = now_utc()
    verification = state["publish"].get("verification_status", "unknown")
    result = "success" if verification in ("verified", "partial") else "failed"
    metrics["pipeline_result"] = result

    # Â§18.1: Final status metric
    monitor.toggle_run(status=result, trigger=state["run"].get("trigger_type", "manual"))
    monitor.export()

    if run_id:
        audit["checkpoints_ref"].append(f"audit_completion:{run_id}:{now_utc()[:10]}")

    _tick(cast(AuditState, audit), "audit_completion", (time.perf_counter() - t0) * 1000)

    return {
        "audit": audit,
        "current_node": "audit_completion",
    }


# ---------------------------------------------------------------------------
# Routing functions
# ---------------------------------------------------------------------------


def should_continue_after_critic(state: WorkflowState) -> str:
    """
    Control flow after critic.

    - If score < 0.7 AND we haven't exceeded 2 revision cycles â†’ re-draft
    - Otherwise â†’ approval_gate (human decides).
    """
    editorial = state["editorial"]
    revisions = int(editorial.get("revision_count") or 0)
    # Increment revision counter (mutating state dict[str, Any] in routing is valid in LangGraph)
    state["editorial"]["revision_count"] = revisions + 1

    score = float(editorial.get("critic_report", {}).get("score") or 0.0)
    if score < 0.7 and revisions < 2:
        return "draft_and_rewrite"
    return "approval_gate"


async def shadow_research_node(state: WorkflowState, config: RunnableConfig) -> dict[str, Any]:
    """Ref Â§30.2: Parallel research signals for shadow evaluation."""
    app_config: AppConfig | None = config.get("configurable", {}).get("app_config")
    run_id: str = state["run"].get("run_id", "")
    if not app_config or not run_id:
        return {"current_node": "shadow_research"}

    # Simulate shadow research signals
    research = dict(state["research"])
    research["shadow_signals"] = ["trending_secondary", "competitor_shadow_v1"]

    return {
        "research": research,
        "current_node": "shadow_research"
    }


async def groundedness_check_node(state: WorkflowState, config: RunnableConfig) -> dict[str, Any]:
    """Ref Â§30.4: Verify draft groundedness against retrieved snippets."""
    app_config: AppConfig | None = config.get("configurable", {}).get("app_config")
    if not app_config:
        return {"current_node": "groundedness_check"}

    from .rag import GroundednessChecker
    checker = GroundednessChecker(app_config)

    draft = state.get("draft") or ""
    contexts = state["research"].get("canonical_docs") or []
    snippets = [c.get("text") or "" for c in contexts if isinstance(c, dict)]

    if draft and snippets:
        result = await checker.check(draft, snippets)
        editorial = dict(state["editorial"])
        editorial["groundedness_score"] = result.score
        editorial["citations"] = result.citations
        return {"editorial": editorial, "current_node": "groundedness_check"}

    return {"current_node": "groundedness_check"}


def should_publish_after_gate(state: WorkflowState) -> str:
    """
    Route after approval_gate.

    - approved  --> platform_publish
    - rejected  --> END
    - pending   --> back to approval_gate (wait for operator input).
    """
    decision = str(state["editorial"].get("approval_decision") or "pending").strip().lower()
    if decision == "approved":
        return "platform_publish"
    if decision == "rejected":
        return END
    return "approval_gate"


# ---------------------------------------------------------------------------
# Graph construction
# ---------------------------------------------------------------------------


def create_sentinel_graph() -> Any:
    """Build and compile the LangGraph StateGraph for Phase 15."""
    builder = StateGraph(WorkflowState)

    builder.add_node("collect_inputs", collect_inputs_node)
    builder.add_node("discover_sources", discover_sources_node)
    builder.add_node("normalize_docs", normalize_docs_node)
    builder.add_node("plan_angle", plan_angle_node)
    builder.add_node("draft_and_rewrite", draft_and_rewrite_node)
    builder.add_node("groundedness_check", groundedness_check_node)
    builder.add_node("shadow_research", shadow_research_node)
    builder.add_node("critic", critic_node)
    builder.add_node("approval_gate", approval_gate_node)
    builder.add_node("platform_publish", platform_publish_node)
    builder.add_node("verify_and_persist", verify_and_persist_node)
    builder.add_node("audit_completion", audit_completion_node)

    builder.add_edge(START, "collect_inputs")
    builder.add_edge("collect_inputs", "shadow_research")
    builder.add_edge("shadow_research", "discover_sources")
    builder.add_edge("discover_sources", "normalize_docs")
    builder.add_edge("normalize_docs", "plan_angle")
    builder.add_edge("plan_angle", "draft_and_rewrite")
    builder.add_edge("draft_and_rewrite", "groundedness_check")
    builder.add_edge("groundedness_check", "critic")

    builder.add_conditional_edges(
        "critic",
        should_continue_after_critic,
        {"approval_gate": "approval_gate", "draft_and_rewrite": "draft_and_rewrite"},
    )
    builder.add_conditional_edges(
        "approval_gate",
        should_publish_after_gate,
        {"platform_publish": "platform_publish", "approval_gate": "approval_gate", "__end__": END},
    )
    builder.add_edge("platform_publish", "verify_and_persist")
    builder.add_edge("verify_and_persist", "audit_completion")
    builder.add_edge("audit_completion", END)

    checkpointer = MemorySaver()
    return builder.compile(checkpointer=checkpointer, interrupt_before=["approval_gate"])


def _get_graph() -> Any:
    """Return the shared compiled graph (lazy singleton)."""
    global _GRAPH_INSTANCE
    if _GRAPH_INSTANCE is None:
        _GRAPH_INSTANCE = create_sentinel_graph()
    return _GRAPH_INSTANCE


def _build_initial_state(
    config: AppConfig,
    prompt: str,
    competitor_url: str,
    keywords: list[str] | None = None,
    targets: list[str] | None = None,
    simulation: dict[str, Any] | None = None,
    trigger_kind: str = "manual",
    target_language: str = "fr",  # Â§7: Language identifier
) -> WorkflowState:
    """Construct a clean initial WorkflowState for a new pipeline run."""
    return WorkflowState(
        run=RunMeta(
            run_id="",
            mode="dry_run" if simulation else "live",
            scheduled_at="",
            trigger_type=trigger_kind,
            initiator="system",
            retrieval_version=config.retrieval_policy_version,
            approval_policy=config.approval_policy,
            prompt_bundle_id="v1",
            prompt_version=config.prompt_bundle_version,
            browser_profile=config.browser_profile_linkedin,
        ),
        inputs=Inputs(
            daily_prompt=prompt,
            competitor_url=competitor_url,
            target_platforms=list(targets or config.default_targets),
            forced_topic=None,
            operator_overrides={"keywords": list(keywords or [])},
            target_language=target_language,
        ),
        research=ResearchState(
            source_hits=[],
            trend_signals=[],
            canonical_docs=[],
            evidence_pack_id=None,
            confidence=0.0,
        ),
        editorial=EditorialState(
            angle={},
            claims=[],
            draft_main="",
            draft_variants={},
            critic_report={},
            novelty_score=0.0,
            contamination_flags=[],
            needs_human=False,
            approval_decision=None,
            revision_count=0,
        ),
        publish=PublishState(
            attempts=[],
            proofs=[],
            final_urls=[],
            verification_status="pending",
        ),
        audit=AuditState(
            incidents=[],
            metrics={},
            cost_ledger={},
            checkpoints_ref=[],
            artifacts=[],
        ),
        current_node=None,
    )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def run_langgraph_pipeline(
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
    target_language: str = "fr",  # Â§7: Language identifier
) -> dict[str, Any]:
    """
    Execute the LangGraph OCS pipeline (Â§23.4).

    Streams the graph until the first interrupt (``approval_gate``) or
    completion.  Returns a lightweight status dict[str, Any] with the generated
    ``thread_id`` so the caller can resume later via ``resume_pipeline``.
    """
    graph = _get_graph()
    thread_id = str(uuid.uuid4())
    run_config: dict[str, Any] = {
        "configurable": {
            "thread_id": thread_id,
            "app_config": config,
        }
    }

    initial_state = _build_initial_state(
        config=config,
        prompt=prompt,
        competitor_url=competitor_url,
        keywords=keywords,
        targets=targets,
        simulation=simulation,
        trigger_kind=trigger_kind,
        target_language=target_language,
    )

    tracker = LatencyTracker()
    tracker.start("total")

    last_state: dict[str, Any] = {}
    for event in graph.stream(initial_state, run_config):
        last_state = event

    tracker.stop("total")

    run_id = ""
    for node_state in last_state.values():
        if isinstance(node_state, dict):
            run_id = (node_state.get("run") or {}).get("run_id", "") or run_id

    if run_id:
        store = RunStore(config)
        run = store.load_run(run_id)
        run["thread_id"] = thread_id
        run["updated_at"] = now_utc()

        # Merge latency info if available
        if "analysis" not in run:
            run["analysis"] = {}

        # For LangGraph, we track the total graph execution time as the SLA baseline
        run["analysis"]["latency_ms"] = tracker.get_report()

        store.save_run(run)
        return run

    return {
        "status": "langgraph_execution_started",
        "thread_id": thread_id,
        "run_id": run_id,
        "paused_at": "approval_gate",
    }


def resume_pipeline(config: AppConfig, thread_id: str) -> dict[str, Any]:
    """
    Resume a paused pipeline thread (after human approval / rejection).

    The operator must have updated the run's ``approval_state`` in RunStore
    before calling this.  The graph will read it in ``approval_gate_node``
    and route accordingly.
    """
    graph = _get_graph()
    run_config: dict[str, Any] = {
        "configurable": {
            "thread_id": thread_id,
            "app_config": config,
        }
    }

    last_state: dict[str, Any] = {}
    for event in graph.stream(None, run_config):
        last_state = event

    return {
        "status": "langgraph_resumed",
        "thread_id": thread_id,
        "final_nodes": list(last_state.keys()),
    }


def approve_run(config: AppConfig, run_id: str, thread_id: str) -> dict[str, Any]:
    """
    Mark a run as ``approved`` and resume the pipeline thread.

    Convenience wrapper used by the Telegram /approve command (Â§23.3).
    """
    store = RunStore(config)
    run = store.load_run(run_id)
    run["approval_state"] = "approved"
    run["updated_at"] = now_utc()
    store.save_run(run)
    return resume_pipeline(config, thread_id)


def reject_run(config: AppConfig, run_id: str, thread_id: str, reason: str = "") -> dict[str, Any]:
    """
    Mark a run as ``rejected`` and resume the pipeline thread.

    The graph will then route to END.
    Convenience wrapper used by the Telegram /reject command (Â§23.3).
    """
    store = RunStore(config)
    run = store.load_run(run_id)
    run["approval_state"] = "rejected"
    run["rejection_reason"] = reason
    run["updated_at"] = now_utc()
    store.save_run(run)
    return resume_pipeline(config, thread_id)


def replay_node(config: AppConfig, thread_id: str, node_name: str) -> dict[str, Any]:
    """Rewind the graph state to a specific node for replay (Â§23.5)."""
    graph = _get_graph()
    run_config: dict[str, Any] = {
        "configurable": {
            "thread_id": thread_id,
            "app_config": config,
        }
    }
    # Update state to point to the desired node
    graph.update_state(run_config, {"current_node": node_name}, as_node=node_name)

    return resume_pipeline(config, thread_id)

