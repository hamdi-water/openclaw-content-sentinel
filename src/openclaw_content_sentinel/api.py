from __future__ import annotations

import asyncio
from pathlib import Path
from time import perf_counter
from typing import TYPE_CHECKING, Any
from uuid import uuid4

from .api_state import ApiStateStore
from .browser_automation import publish_via_browser
from .competitor import extract_article
from .compliance import build_compliance_report
from .config import AppConfig
from .dashboard import build_dashboard_summary
from .gap_tracker import build_v61_gap_tracker
from .images import build_render_asset_pack
from .ledger import ledger_summary
from .operations import build_scheduler_health_report
from .source_registry import build_source_registry_report, load_source_registry
from .storage import RunStore
from .utils import dump_json, now_utc
from .webhooks import WebhookDispatcher
from .workflow import (
    approve_run,
    build_telegram_preview,
    daily_run,
    doctor_report,
    load_daily_brief_queue,
    load_daily_input,
    maybe_send_telegram_preview,
    pause_automation,
    promote_daily_brief,
    refresh_run_state,
    reject_run,
    resume_automation,
    review_run,
)

if TYPE_CHECKING:
    from fastapi import FastAPI, HTTPException, Request, Response
    from fastapi.responses import FileResponse
    from pydantic import BaseModel, Field
else:
    try:
        from fastapi import FastAPI, HTTPException, Request, Response
        from fastapi.responses import FileResponse
        from pydantic import BaseModel, Field
    except Exception:  # pragma: no cover
        FastAPI = Any  # type: ignore[assignment]
        HTTPException = Any  # type: ignore[assignment]
        Request = Any  # type: ignore[assignment]
        Response = Any  # type: ignore[assignment]
        FileResponse = Any  # type: ignore[assignment]
        BaseModel = Any  # type: ignore[assignment]
        Field = Any  # type: ignore[assignment]


class RunCreateRequest(BaseModel):
    prompt: str = ""
    competitor_url: str
    keywords: list[str] = Field(default_factory=list[Any])
    targets: list[str] = Field(default_factory=lambda: ["linkedin", "facebook", "x"])
    execute_pipeline: bool = True
    send_telegram_preview: bool = True
    ignore_pause: bool = False
    schema_version: str = "v6.1"


class ProjectRequest(BaseModel):
    name: str
    description: str = ""


class BrandProfileRequest(BaseModel):
    profile_id: str = "default"
    name: str = "Default brand profile"
    content: str
    language: str = "fr"  # §7: Preferred language (fr, en, etc)
    active: bool = True


class SourceConfigRequest(BaseModel):
    source_id: str
    type: str
    domains: list[str] = Field(default_factory=list[Any])
    acquisition: str = ""
    priority: int = 5
    legal_notes: str = ""


class SourceIngestRequest(BaseModel):
    competitor_url: str = ""
    run_id: str = ""


class CandidateRequest(BaseModel):
    run_id: str
    variant_id: str = "master"


class ApprovalDecisionRequest(BaseModel):
    run_id: str
    decision: str
    note: str = ""
    candidate_ref: str = ""


class ApproveRequest(BaseModel):
    note: str = ""
    auto_publish: bool = False


class ReviseRequest(BaseModel):
    note: str = ""


class DraftPatchRequest(BaseModel):
    drafts: dict[str, str]
    refresh_review: bool = True


class PublishJobRequest(BaseModel):
    run_id: str
    platforms: list[str] = Field(default_factory=lambda: ["linkedin", "facebook", "x"])
    submit: bool = True
    continue_on_error: bool = True
    retries: int | None = None
    idempotency_key: str = ""
    candidate_ref: str = ""
    approval_ref: str = ""
    schema_version: str = "v6.1"


class CampaignRequest(BaseModel):
    name: str
    objective: str = ""
    frequency: str = "daily"
    publish_windows: list[str] = Field(default_factory=list[Any])
    approval_policy: str = ""
    target_platforms: list[str] = Field(default_factory=lambda: ["linkedin", "facebook", "x"])
    project_id: str = "proj_default"
    active: bool = True


class TemplateRequest(BaseModel):
    template_id: str
    name: str
    asset_type: str
    layout: str
    dimensions: dict[str, Any] = Field(default_factory=dict[str, Any])
    platforms: list[str] = Field(default_factory=list[Any])
    style_tokens: list[str] = Field(default_factory=list[Any])
    render_backend: str = "procedural"
    default_slide_count: int | None = None


class RenderJobRequest(BaseModel):
    run_id: str
    asset_type: str = "social_card"
    template_id: str = ""
    slide_count: int = 4
    submit: bool = True
    idempotency_key: str = ""


class ReplayRequest(BaseModel):
    run_id: str
    stages: list[str] = Field(default_factory=lambda: ["refresh_state"])
    replay_window: str = "bounded"


class SchedulePatchRequest(BaseModel):
    auto_publish_enabled: bool = True
    approval_required: bool = True
    daily_cron_enabled: bool = True
    heartbeat_enabled: bool = True
    operator_timezone: str = "Africa/Tunis"
    automation_paused: bool | None = None
    reason: str = ""
    active_brief_id: str = ""


class ScheduleStatePatchRequest(BaseModel):
    automation_paused: bool | None = None
    reason: str = ""
    active_brief_id: str = ""


class WebhookTestRequest(BaseModel):
    event: str = "test"
    payload: dict[str, Any] = Field(default_factory=dict[str, Any])


class WebhookCreateRequest(BaseModel):
    url: str
    secret: str
    events: list[str] = Field(default_factory=list[Any])


def _require_fastapi() -> None:
    if FastAPI is None:  # pragma: no cover
        msg = (
            "FastAPI is not installed. Install the optional `api` extra to enable the internal API."
        )
        raise RuntimeError(msg)


def _load_source_registry(config: AppConfig) -> dict:
    return load_source_registry(config)


def _write_source_registry(config: AppConfig, payload: dict[str, Any]) -> dict:
    dump_json(config.source_registry_file, payload)
    return _load_source_registry(config)


def _request_meta(request: Request) -> tuple[str, str, str]:
    headers = request.headers
    request_id = (
        headers.get("X-Request-Id") or headers.get("x-request-id") or uuid4().hex[:16]
    ).strip()
    actor = (
        headers.get("X-Actor") or headers.get("x-actor") or "internal-api"
    ).strip() or "internal-api"
    idempotency_key = (
        headers.get("Idempotency-Key") or headers.get("idempotency-key") or ""
    ).strip()
    return request_id, actor, idempotency_key


def _platform_job_state(run: dict[str, Any], platform: str) -> tuple[str, str, int]:
    result = (run.get("post_results") or {}).get(platform) or {}
    proof = ((run.get("proof_pack") or {}).get("results") or {}).get(platform) or {}
    status = str(result.get("status") or "pending").strip().lower()
    state_map = {
        "posted": "VERIFIED",
        "failed": "FAILED",
        "ready_to_publish": "QUEUED",
        "pending": "QUEUED",
        "posting": "POST_ATTEMPTED",
    }
    return (
        state_map.get(status, status.upper() or "QUEUED"),
        str(proof.get("proof_id") or ""),
        int(result.get("attempts", 0) or 0),
    )


def _update_job_from_run(
    api_state: ApiStateStore, job: dict[str, Any], run: dict[str, Any]
) -> dict:
    platform = str(job.get("platform") or "")
    state, proof_id, attempt_count = _platform_job_state(run, platform)
    result = (run.get("post_results") or {}).get(platform) or {}
    return api_state.update_publish_job(
        str(job.get("job_id") or ""),
        state=state,
        final_url=str(result.get("url") or ""),
        proof_id=proof_id,
        attempt_count=attempt_count,
        last_error=str(result.get("failure_reason") or result.get("note") or ""),
    )


def _capture_analytics_snapshot(
    api_state: ApiStateStore,
    runtime_config: AppConfig,
    *,
    actor: str = "system",
    request_id: str = "",
    run_id: str = "",
) -> dict[str, Any]:
    from .compliance import build_compliance_report
    from .gap_tracker import build_v61_gap_tracker

    payload = {
        "dashboard": build_dashboard_summary(runtime_config),
        "compliance": build_compliance_report(runtime_config),
        "gap_tracker": build_v61_gap_tracker(runtime_config),
    }
    scope = "run" if run_id else "system"
    return api_state.create_analytics_snapshot(
        scope=scope,
        payload=payload,
        run_id=run_id,
        actor=actor,
        request_id=request_id,
    )


def _execute_render_job(
    runtime_config: AppConfig, run: dict[str, Any], render_job: dict[str, Any]
) -> dict[str, Any]:
    store = RunStore(runtime_config)
    render_root = (
        store.run_dir(str(run.get("run_id") or ""))
        / "media"
        / "render-jobs"
        / str(render_job.get("render_job_id") or "")
    )
    return build_render_asset_pack(
        run,
        render_root,
        config=runtime_config,
        asset_type=str(render_job.get("asset_type") or "social_card"),
        template_id=str(render_job.get("template_id") or ""),
        slide_count=int(render_job.get("slide_count") or 4),
    )


def build_app(config: AppConfig | None = None) -> FastAPI:
    _require_fastapi()
    runtime_config = config or AppConfig.from_env(Path.cwd())
    store = RunStore(runtime_config)
    store.bootstrap()
    api_state = ApiStateStore(runtime_config)
    api_state.bootstrap()
    app = FastAPI(title="OpenClaw Content Sentinel Internal API", version="v1")
    from fastapi.middleware.cors import CORSMiddleware

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.middleware("http")
    async def add_security_headers(request: Request, call_next: Any) -> Response:
        response = await call_next(request)
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
            "img-src 'self' data: https:; font-src 'self'; connect-src 'self';"
        )
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-XSS-Protection"] = "1; mode=block"
        response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
        return response

    @app.middleware("http")
    async def audit_requests(request: Request, call_next: Any) -> Response:
        request_id, actor, idempotency_key = _request_meta(request)
        started = perf_counter()
        response = None
        status_code = 500
        result = "error"
        try:
            response = await call_next(request)
            status_code = response.status_code
            result = "ok" if status_code < 400 else "error"
            response.headers["X-Request-Id"] = request_id
            return response
        finally:
            latency_ms = int((perf_counter() - started) * 1000)
            api_state.log_request(
                request_id=request_id,
                actor=actor,
                method=request.method,
                path=str(request.url.path),
                status_code=status_code,
                latency_ms=latency_ms,
                idempotency_key=idempotency_key,
                target_resource=str(request.url.path),
                result=result,
            )

    setup_routes(app, runtime_config, store, api_state)
    return app


def setup_routes(
    app: FastAPI, runtime_config: AppConfig, store: RunStore, api_state: ApiStateStore
) -> FastAPI:
    _setup_project_routes(app, runtime_config, api_state)
    _setup_brand_routes(app, runtime_config, api_state)
    _setup_campaign_routes(app, runtime_config, api_state)
    _setup_template_routes(app, runtime_config, api_state)
    _setup_source_routes(app, runtime_config, store)
    _setup_run_routes(app, runtime_config, store)
    _setup_candidate_routes(app, runtime_config, api_state, store)
    _setup_approval_routes(app, runtime_config, api_state, store)
    _setup_draft_routes(app, runtime_config, api_state, store)
    _setup_job_routes(app, runtime_config, store, api_state)
    _setup_analytics_routes(app, runtime_config, api_state)
    _setup_webhook_routes(app, runtime_config)
    return app


def _setup_project_routes(app: FastAPI, config: AppConfig, api_state: ApiStateStore) -> None:
    @app.get("/v1/projects")
    def list_projects() -> dict[str, Any]:
        items = api_state.list_projects()
        return {"items": items, "count": len(items), "schema_version": config.schema_version}

    @app.post("/v1/projects")
    def create_project_endpoint(request: ProjectRequest) -> dict[str, Any]:
        return api_state.create_project(request.name, description=request.description)


def _setup_brand_routes(app: FastAPI, config: AppConfig, api_state: ApiStateStore) -> None:
    @app.get("/v1/brand-profiles")
    def list_brand_profiles() -> dict[str, Any]:
        items = api_state.list_brand_profiles()
        return {"items": items, "count": len(items), "schema_version": config.schema_version}

    @app.post("/v1/brand-profiles")
    def create_brand_profile(request: BrandProfileRequest) -> dict[str, Any]:
        return api_state.upsert_brand_profile(
            profile_id=request.profile_id,
            name=request.name,
            content=request.content,
            active=request.active,
        )

    @app.patch("/v1/brand-profiles/{profile_id}")
    def patch_brand_profile(profile_id: str, request: BrandProfileRequest) -> dict[str, Any]:
        return api_state.upsert_brand_profile(
            profile_id=profile_id,
            name=request.name,
            content=request.content,
            active=request.active,
        )


def _setup_campaign_routes(app: FastAPI, config: AppConfig, api_state: ApiStateStore) -> None:
    @app.get("/v1/campaigns")
    def list_campaigns() -> dict[str, Any]:
        items = api_state.list_campaigns()
        return {"items": items, "count": len(items), "schema_version": config.schema_version}

    @app.post("/v1/campaigns")
    def create_campaign_endpoint(request: CampaignRequest) -> dict[str, Any]:
        return api_state.create_campaign(**request.model_dump())

    @app.patch("/v1/campaigns/{campaign_id}")
    def patch_campaign(campaign_id: str, request: CampaignRequest) -> dict[str, Any]:
        try:
            return api_state.update_campaign(campaign_id, request.model_dump())
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc


def _setup_template_routes(app: FastAPI, config: AppConfig, api_state: ApiStateStore) -> None:
    @app.get("/v1/templates")
    def list_templates(asset_type: str = "") -> dict[str, Any]:
        items = api_state.list_templates(asset_type=asset_type)
        return {"items": items, "count": len(items), "schema_version": config.schema_version}

    @app.post("/v1/templates")
    def create_template(request: TemplateRequest) -> dict[str, Any]:
        return api_state.upsert_template(**request.model_dump())

    @app.patch("/v1/templates/{template_id}")
    def patch_template(template_id: str, request: TemplateRequest) -> dict[str, Any]:
        payload = request.model_dump()
        payload["template_id"] = template_id
        return api_state.upsert_template(**payload)


def _setup_source_routes(app: FastAPI, config: AppConfig, store: RunStore) -> None:
    @app.get("/v1/sources")
    def list_sources() -> dict[str, Any]:
        return {
            "registry": load_source_registry(config),
            "report": build_source_registry_report(config),
            "schema_version": config.schema_version,
        }

    @app.post("/v1/sources")
    def create_source(request: SourceConfigRequest) -> dict[str, Any]:
        payload = load_source_registry(config)
        items = list(payload.get("sources") or [])
        source = request.model_dump()
        if any(item.get("source_id") == source["source_id"] for item in items):
            msg = f"Source {source['source_id']} already exists."
            raise HTTPException(status_code=409, detail=msg)
        items.append(source)
        from .source_registry import write_source_registry

        return write_source_registry(config, {"sources": items})

    @app.patch("/v1/sources/{source_id}")
    def patch_source(source_id: str, request: SourceConfigRequest) -> dict[str, Any]:
        payload = load_source_registry(config)
        items = list(payload.get("sources") or [])
        source = request.model_dump()
        for i, item in enumerate(items):
            if item.get("source_id") == source_id:
                items[i] = {**item, **source, "source_id": source_id}
                from .source_registry import write_source_registry

                return write_source_registry(config, {"sources": items})
        raise HTTPException(status_code=404, detail=f"Unknown source: {source_id}")

    @app.post("/v1/sources/ingest")
    def ingest_source_endpoint(request: SourceIngestRequest) -> dict[str, Any]:
        if request.run_id:
            try:
                run = store.load_run(request.run_id)
            except FileNotFoundError as exc:
                raise HTTPException(status_code=404, detail=str(exc)) from exc
            c_url = str(request.competitor_url or run.get("competitor_url") or "").strip()
            article = extract_article(c_url, config).to_dict()
            return {
                "run_id": request.run_id,
                "article": article,
                "schema_version": config.schema_version,
            }
        if not request.competitor_url:
            raise HTTPException(status_code=400, detail="Provide either run_id or competitor_url.")
        article = extract_article(request.competitor_url, config).to_dict()
        return {"article": article, "schema_version": config.schema_version}


def _create_run_handler(
    request: RunCreateRequest, config: AppConfig, store: RunStore
) -> dict[str, Any]:
    if request.execute_pipeline:
        run = daily_run(
            config,
            **request.model_dump(exclude={"execute_pipeline", "send_telegram_preview"}),
            trigger_kind="api",
        )
        if request.send_telegram_preview:
            maybe_send_telegram_preview(config, run["run_id"])
            run = store.load_run(run["run_id"])
        return run
    from .workflow import create_run

    return create_run(
        config,
        **request.model_dump(exclude={"execute_pipeline", "send_telegram_preview"}),
        trigger_kind="api",
    )


def _setup_run_routes(app: FastAPI, config: AppConfig, store: RunStore) -> None:
    @app.get("/v1/runs")
    def list_runs(limit: int = 10) -> dict[str, Any]:
        runs = store.list_runs()[: max(1, min(limit, 100))]
        return {"items": runs, "count": len(runs), "schema_version": config.schema_version}

    @app.post("/v1/runs")
    def create_run_endpoint(request: RunCreateRequest) -> dict[str, Any]:
        return _create_run_handler(request, config, store)

    @app.get("/v1/runs/{run_id}")
    def get_run(run_id: str) -> dict[str, Any]:
        try:
            return store.load_run(run_id)
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc


def _setup_candidate_routes(
    app: FastAPI, config: AppConfig, api_state: ApiStateStore, store: RunStore
) -> None:
    @app.post("/v1/candidates")
    def create_candidate_endpoint(request: CandidateRequest) -> dict[str, Any]:
        try:
            run = store.load_run(request.run_id)
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return {
            "candidate": api_state.create_candidate(run, variant_id=request.variant_id),
            "schema_version": config.schema_version,
        }

    @app.get("/v1/candidates")
    def list_candidates(run_id: str = "") -> dict[str, Any]:
        items = api_state.list_candidates(run_id=run_id)
        return {"items": items, "count": len(items), "schema_version": config.schema_version}


def _setup_approval_routes(
    app: FastAPI, config: AppConfig, api_state: ApiStateStore, store: RunStore
) -> None:
    @app.post("/v1/approvals")
    def create_approval_endpoint(
        request: ApprovalDecisionRequest, http_request: Request
    ) -> dict[str, Any]:
        req_id, actor, _ = _request_meta(http_request)
        try:
            run = store.load_run(request.run_id)
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        c_ref = request.candidate_ref or api_state.create_candidate(run)["candidate_id"]
        decision = str(request.decision or "").strip().lower()
        if decision not in {"approve", "approved", "reject", "rejected", "revise"}:
            raise HTTPException(status_code=400, detail="Decision must be approve or reject.")
        if decision in {"approve", "approved"}:
            upd_run = approve_run(config, request.run_id, note=request.note, operator_id=actor)
            status = "approved"
        else:
            upd_run = reject_run(config, request.run_id, note=request.note, operator_id=actor)
            status = "rejected"
        approval = api_state.create_approval_record(
            request.run_id, c_ref, status, note=request.note, actor=actor, request_id=req_id
        )
        return {"approval": approval, "run": upd_run}

    @app.post("/v1/runs/{run_id}/approve")
    def approve_endpoint(
        run_id: str, request: ApproveRequest, http_request: Request
    ) -> dict[str, Any]:
        req_id, actor, id_key = _request_meta(http_request)
        try:
            run = store.load_run(run_id)
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        cand = api_state.create_candidate(run)
        approval = api_state.create_approval_record(
            run_id,
            cand["candidate_id"],
            "approved",
            note=request.note,
            actor=actor,
            request_id=req_id,
        )
        res: dict[str, Any] = {
            "approval": approval,
            "run": approve_run(config, run_id, note=request.note, operator_id=actor),
        }

        # §33.5: Trigger webhook on approval

        dispatcher = WebhookDispatcher(config)
        event_payload = {"run_id": run_id, "note": request.note}
        asyncio.create_task(dispatcher.dispatch("run_approved", event_payload))

        if request.auto_publish:
            job_req = PublishJobRequest(
                run_id=run_id,
                idempotency_key=id_key or uuid4().hex[:16],
                submit=True,
                continue_on_error=True,
            )
            res["publish"] = _create_publish_job_handler(
                job_req, config, store, api_state, http_request
            )
        return res

    @app.post("/v1/runs/{run_id}/revise")
    def revise_endpoint(
        run_id: str, request: ReviseRequest, http_request: Request
    ) -> dict[str, Any]:
        req_id, actor, _ = _request_meta(http_request)
        try:
            run = store.load_run(run_id)
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        cand = api_state.create_candidate(run)
        approval = api_state.create_approval_record(
            run_id,
            cand["candidate_id"],
            "rejected",
            note=request.note,
            actor=actor,
            request_id=req_id,
        )
        return {
            "approval": approval,
            "run": reject_run(config, run_id, note=request.note, operator_id=actor),
        }


def _setup_draft_routes(
    app: FastAPI, config: AppConfig, api_state: ApiStateStore, store: RunStore
) -> None:
    @app.get("/v1/drafts/{run_id}")
    def get_drafts(run_id: str) -> dict[str, Any]:
        try:
            run = store.load_run(run_id)
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        names = ["article", "linkedin", "facebook", "x", "image_prompt"]
        run_dir = store.run_dir(run_id)
        drafts = {
            n: (run_dir / "drafts" / f"{n}.md").read_text(encoding="utf-8")
            if (run_dir / "drafts" / f"{n}.md").exists()
            else ""
            for n in names
        }
        return {
            "run_id": run_id,
            "drafts": drafts,
            "schema_version": run.get("schema_version", ""),
        }

    _setup_draft_patch_route(app, config, store)
    _setup_draft_preview_route(app, store)


def _setup_draft_patch_route(app: FastAPI, config: AppConfig, store: RunStore) -> None:
    @app.patch("/v1/drafts/{run_id}")
    def patch_drafts(run_id: str, request: DraftPatchRequest) -> dict[str, Any]:
        try:
            store.load_run(run_id)
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        run_dir = store.run_dir(run_id)
        for name, content in request.drafts.items():
            if name in {"article", "linkedin", "facebook", "x", "image_prompt"}:
                path = run_dir / "drafts" / f"{name}.md"
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(str(content or ""), encoding="utf-8")
        if request.refresh_review:
            refresh_run_state(config, run_id)
            review_run(config, run_id)
        return {"status": "ok", "run_id": run_id}


def _setup_draft_preview_route(app: FastAPI, store: RunStore) -> None:
    @app.post("/v1/runs/{run_id}/preview")
    def create_preview_endpoint(run_id: str) -> dict[str, Any]:
        try:
            run = store.load_run(run_id)
            return {"run": run, "telegram_preview": build_telegram_preview(run)}
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.get("/v1/assets/{run_id}")
    def list_assets(run_id: str) -> dict[str, Any]:
        try:
            run = store.load_run(run_id)
            return {
                "run_id": run_id,
                "assets": store.list_artifacts(run_id),
                "schema_version": run.get("schema_version", ""),
            }
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.get("/v1/assets/{run_id}/{asset_path:path}")
    def get_asset(run_id: str, asset_path: str) -> FileResponse:
        try:
            run_root = store.run_dir(run_id).resolve()
            candidate = (run_root / asset_path).resolve()
            if run_root not in candidate.parents and candidate != run_root:
                raise HTTPException(status_code=400, detail="Asset path escapes run directory.")
            if not candidate.exists() or not candidate.is_file():
                raise HTTPException(status_code=404, detail="Asset not found.")
            return FileResponse(candidate)
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc


def _create_publish_job_handler(
    request: PublishJobRequest,
    config: AppConfig,
    store: RunStore,
    api_state: ApiStateStore,
    http_request: Request,
) -> dict[str, Any]:
    req_id, actor, h_id_key = _request_meta(http_request)
    id_key = str(request.idempotency_key or h_id_key or "").strip()
    if not id_key:
        raise HTTPException(status_code=400, detail="Idempotency key required.")
    try:
        run = store.load_run(request.run_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    platforms = list(request.platforms or run.get("platform_targets") or [])
    c_id = request.candidate_ref or api_state.create_candidate(run)["candidate_id"]
    a_id = request.approval_ref or (api_state.latest_approval_for_run(request.run_id) or {}).get(
        "approval_id", ""
    )
    jobs = api_state.create_publish_job_records(
        run=run,
        platforms=platforms,
        candidate_ref=c_id,
        approval_ref=a_id,
        actor=actor,
        request_id=req_id,
        idempotency_seed=id_key,
    )
    results = []
    res_jobs = {}
    staged = False
    for platform, job in zip(platforms, jobs, strict=False):
        try:
            api_state.update_publish_job(job["job_id"], state="QUEUED")
            if request.submit:
                results.append(
                    publish_via_browser(
                        config,
                        request.run_id,
                        platform,
                        submit=True,
                        retries=request.retries,
                    )
                )
                res_jobs[platform] = _update_job_from_run(
                    api_state, job, store.load_run(request.run_id)
                )
            else:
                res_jobs[platform] = _update_job_from_run(
                    api_state, job, store.load_run(request.run_id)
                )
        except Exception as exc:
            res_jobs[platform] = api_state.update_publish_job(
                job["job_id"], state="FAILED", last_error=str(exc)
            )
            if not request.continue_on_error:
                raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {
        "job": jobs[0] if jobs else {},
        "jobs": list(res_jobs.values()) if res_jobs else jobs,
        "results": list(res_jobs.values()),
        "staged": staged,
        "publish_results": results,
        "schema_version": config.schema_version,
    }


def _setup_job_routes(
    app: FastAPI, config: AppConfig, store: RunStore, api_state: ApiStateStore
) -> None:
    @app.get("/v1/render-jobs")
    def list_render_jobs(run_id: str = "", limit: int = 50) -> dict[str, Any]:
        items = api_state.list_render_jobs(run_id=run_id, limit=limit)
        return {"items": items, "count": len(items), "schema_version": config.schema_version}

    @app.get("/v1/render-jobs/{render_job_id}")
    def get_render_job(render_job_id: str) -> dict[str, Any]:
        items = api_state.list_render_jobs(limit=500)
        job = next((item for item in items if item.get("render_job_id") == render_job_id), None)
        if not job:
            raise HTTPException(status_code=404, detail=f"Unknown render job: {render_job_id}")
        return job

    @app.post("/v1/render-jobs")
    def create_render_job_endpoint(
        request: RenderJobRequest, http_request: Request
    ) -> dict[str, Any]:
        req_id, actor, h_id_key = _request_meta(http_request)
        try:
            run = store.load_run(request.run_id)
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        id_seed = str(request.idempotency_key or h_id_key or "").strip()
        job = api_state.create_render_job(
            run=run,
            asset_type=request.asset_type,
            template_id=request.template_id,
            slide_count=request.slide_count,
            actor=actor,
            request_id=req_id,
            idempotency_seed=id_seed,
        )
        if request.submit:
            try:
                res = _execute_render_job(config, run, job)
                job = api_state.update_render_job(
                    str(job["render_job_id"]),
                    state="RENDERED",
                    output_assets=list(res.get("assets") or []),
                    primary_asset=str(res.get("primary_asset") or ""),
                    manifest_path=str(res.get("manifest_path") or ""),
                )
            except Exception as exc:
                api_state.update_render_job(
                    str(job["render_job_id"]), state="FAILED", last_error=str(exc)
                )
                raise HTTPException(status_code=409, detail=str(exc)) from exc
        return {"render_job": job, "schema_version": config.schema_version}

    @app.get("/v1/publish-jobs")
    def list_publish_jobs(run_id: str = "", limit: int = 50) -> dict[str, Any]:
        items = api_state.list_publish_jobs(run_id=run_id, limit=limit)
        return {"items": items, "count": len(items), "schema_version": config.schema_version}

    @app.get("/v1/publish-jobs/{job_id}")
    def get_publish_job(job_id: str) -> dict[str, Any]:
        items = api_state.list_publish_jobs(limit=500)
        job = next((item for item in items if item.get("job_id") == job_id), None)
        if not job:
            raise HTTPException(status_code=404, detail=f"Unknown publish job: {job_id}")
        return job

    @app.post("/v1/publish-jobs")
    def create_publish_job_endpoint(
        request: PublishJobRequest, http_request: Request
    ) -> dict[str, Any]:
        return _create_publish_job_handler(request, config, store, api_state, http_request)

    @app.get("/v1/proofs/{run_id}")
    def get_proofs(run_id: str) -> dict[str, Any]:
        try:
            run = store.load_run(run_id)
            return {
                "run_id": run_id,
                "proof_pack": run.get("proof_pack", {}),
                "identity": run.get("identity", {}),
                "schema_version": run.get("schema_version", config.schema_version),
            }
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc


def _setup_analytics_routes(  # noqa: C901
    app: FastAPI, config: AppConfig, api_state: ApiStateStore
) -> None:
    @app.post("/v1/replays")
    def create_replay(request: ReplayRequest, http_request: Request) -> dict[str, Any]:
        req_id, actor, _ = _request_meta(http_request)
        for stage in request.stages:
            if stage == "refresh_state":
                refresh_run_state(config, request.run_id)
            elif stage == "review":
                refresh_run_state(config, request.run_id)
                review_run(config, request.run_id)
            else:
                raise HTTPException(status_code=400, detail=f"Unsupported replay stage: {stage}")
        replay = api_state.create_replay_record(
            request.run_id, request.stages, request.replay_window, actor=actor, request_id=req_id
        )
        return {"replay": replay, "run": RunStore(config).load_run(request.run_id)}

    @app.get("/v1/maintenance/report")
    def get_maintenance_report() -> dict[str, Any]:
        return {
            "doctor": doctor_report(config),
            "compliance": build_compliance_report(config),
            "audit": ledger_summary(config.ledger_db_path),
            "daily_brief_queue": load_daily_brief_queue(config),
            "scheduler_health": build_scheduler_health_report(config),
            "schema_version": config.schema_version,
        }

    @app.get("/v1/schedules")
    def get_schedules() -> dict[str, Any]:
        from .operations import build_scheduler_health_report

        return {
            "schedule_state": api_state.get_schedule_state(),
            "daily_input": load_daily_input(config),
            "daily_brief_queue": load_daily_brief_queue(config),
            "scheduler_health": build_scheduler_health_report(config),
            "schema_version": config.schema_version,
        }

    @app.patch("/v1/schedules")
    def patch_schedules(request: SchedulePatchRequest, http_request: Request) -> dict[str, Any]:
        req_id, actor, _ = _request_meta(http_request)
        desired_patch = {
            "auto_publish_enabled": request.auto_publish_enabled,
            "approval_required": request.approval_required,
            "daily_cron_enabled": request.daily_cron_enabled,
            "heartbeat_enabled": request.heartbeat_enabled,
            "operator_timezone": request.operator_timezone,
        }
        observed_patch: dict[str, Any] = {}
        if request.automation_paused is True:
            observed_patch["automation_paused"] = bool(
                pause_automation(config, reason=request.reason or "api", updated_by=actor).get(
                    "automation_paused"
                )
            )
        elif request.automation_paused is False:
            observed_patch["automation_paused"] = bool(
                resume_automation(config, reason=request.reason or "api", updated_by=actor).get(
                    "automation_paused"
                )
            )
        return {
            "schedule_state": api_state.update_schedule_state(
                desired_patch=desired_patch,
                observed_patch=observed_patch,
                actor=actor,
                request_id=req_id,
            ),
            "promoted_brief": promote_daily_brief(config, request.active_brief_id)
            if request.active_brief_id
            else {},
            "schema_version": config.schema_version,
        }

    @app.patch("/v1/schedule/state")
    def patch_schedule_state(
        request: ScheduleStatePatchRequest, http_request: Request
    ) -> dict[str, Any]:
        req_id, actor, _ = _request_meta(http_request)
        p, obs = "schedule_state", {}
        if request.automation_paused is True:
            obs["automation_paused"] = bool(
                pause_automation(config, reason=request.reason or "api", updated_by=actor).get(
                    "automation_paused"
                )
            )
        elif request.automation_paused is False:
            obs["automation_paused"] = bool(
                resume_automation(config, reason=request.reason or "api", updated_by=actor).get(
                    "automation_paused"
                )
            )
        return {
            "schedule_state": api_state.update_schedule_state(
                desired_patch=p, observed_patch=obs, actor=actor, request_id=req_id
            ),
            "promoted_brief": promote_daily_brief(config, request.active_brief_id)
            if request.active_brief_id
            else {},
            "schema_version": config.schema_version,
        }

    def _analytics_payload() -> dict[str, Any]:
        return {
            "doctor": doctor_report(config),
            "compliance": build_compliance_report(config),
            "audit": ledger_summary(config.ledger_db_path),
            "gap_tracker": build_v61_gap_tracker(config),
            "latest_snapshot": _capture_analytics_snapshot(api_state, config),
            "request_log": api_state._load_items("request_log")[-50:],
            "schema_version": config.schema_version,
        }

    @app.get("/v1/analytics")
    def get_analytics() -> dict[str, Any]:
        return _analytics_payload()

    @app.get("/v1/analytics/snapshot")
    def get_analytics_snapshot() -> dict[str, Any]:
        return _analytics_payload()

    @app.get("/v1/analytics/snapshots")
    def list_analytics_snapshots(run_id: str = "", limit: int = 50) -> dict[str, Any]:
        items = api_state.list_analytics_snapshots(run_id=run_id, limit=limit)
        return {
            "items": items,
            "count": len(items),
            "schema_version": config.schema_version,
        }

    @app.get("/v1/events")
    def list_events(event_type: str = "", limit: int = 100) -> dict[str, Any]:
        items = api_state.list_events(event_type=event_type, limit=limit)
        return {
            "items": items,
            "count": len(items),
            "schema_version": config.schema_version,
        }

    @app.post("/v1/webhooks/test")
    def test_webhook(request: WebhookTestRequest, http_request: Request) -> dict[str, Any]:
        req_id, actor, _ = _request_meta(http_request)
        return {
            "ok": True,
            "received_at": now_utc(),
            "request_id": req_id,
            "actor": actor,
            "event": request.event,
            "payload": request.payload,
            "event_log": api_state.log_event(
                event_type=request.event, payload=request.payload, actor=actor, request_id=req_id
            ),
            "schema_version": config.schema_version,
        }


def _setup_webhook_routes(app: FastAPI, config: AppConfig) -> None:
    from .webhooks import WebhookRegistry

    registry = WebhookRegistry(config)

    @app.get("/v1/webhooks")
    def list_webhooks() -> dict[str, Any]:
        return {"items": registry.list_webhooks(), "schema_version": config.schema_version}

    @app.post("/v1/webhooks")
    def create_webhook(request: WebhookCreateRequest) -> dict[str, Any]:
        return {
            "webhook": registry.register_webhook(request.url, request.secret, request.events),
            "schema_version": config.schema_version,
        }

    @app.delete("/v1/webhooks/{webhook_id}")
    def delete_webhook(webhook_id: str) -> dict[str, Any]:
        return {
            "success": registry.remove_webhook(webhook_id),
            "schema_version": config.schema_version,
        }
