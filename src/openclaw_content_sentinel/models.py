from __future__ import annotations

import hashlib
import uuid
from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class PreferredLanguage(StrEnum):
    FR = "fr"
    EN = "en"
    ES = "es"
    DE = "de"
    UNKNOWN = "unknown"


# §29.2: Standardized document format
class CanonicalDoc(BaseModel):
    id: str = Field(..., description="Unique document ID (hash-based or UUID)")
    source_url: str
    title: str
    raw_content: str
    clean_text: str
    published_at: str
    ingested_at: str
    metadata: dict[str, Any] = Field(default_factory=dict)
    content_hash: str = ""

    @model_validator(mode="after")
    def compute_hashes(self) -> CanonicalDoc:
        if not self.content_hash and self.clean_text:
            self.content_hash = hashlib.sha256(self.clean_text.encode()).hexdigest()
        return self

    def to_dict(self) -> dict[str, Any]:
        return self.model_dump()


# §21.3: Bundle of documents for a specific run
class EvidencePack(BaseModel):
    pack_id: str
    run_id: str
    docs: list[CanonicalDoc] = Field(default_factory=list)
    summary: str = ""
    confidence_score: float = 0.0
    created_at: str = Field(default_factory=lambda: datetime.now().isoformat())

    def to_dict(self) -> dict[str, Any]:
        return self.model_dump()


# §21.2: Post candidate for publication
class PostCandidate(BaseModel):
    candidate_id: str
    run_id: str
    platform: str
    content: str
    image_url: str | None = None
    scheduled_for: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    evidence_id: str | None = Field(
        None, description="Link to specific EvidencePack or CanonicalDoc"
    )

    def to_dict(self) -> dict[str, Any]:
        return self.model_dump()


# §17.1: Canonical Envelope for cross-module messaging
class CanonicalEnvelope(BaseModel):
    model_config = ConfigDict(extra="ignore")

    message_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    version: str = "v1.0"
    created_at: str = Field(default_factory=lambda: datetime.now().isoformat())
    sender: str = "sentinel-core"
    payload_kind: str  # e.g., "CanonicalDoc", "EvidencePack", "PostCandidate"
    payload: CanonicalDoc | EvidencePack | PostCandidate | dict[str, Any]
    metadata: dict[str, Any] = Field(default_factory=dict)


class DailyInput(BaseModel):
    model_config = ConfigDict(extra="allow")
    topic: str = ""
    business_angle: str = ""
    target_audience: str = ""
    preferred_language: str = "fr"  # §7: Support FR/EN
    key_call_to_action: str = ""
    additional_constraints: list[str] = Field(default_factory=list)


class ViralVelocity(BaseModel):
    """Ref §39: Metric tracking growth over time."""

    model_config = ConfigDict(extra="ignore")
    peak_score: float = 0.0
    velocity: float = 0.0  # Growth per hour
    acceleration: float = 0.0
    sustained_period_mins: int = 0


class AnalysisResult(BaseModel):
    model_config = ConfigDict(extra="allow")
    original_angle: str = ""
    suggested_hooks: list[str] = Field(default_factory=list)
    competitor_overlap_score: float = 0.0
    risk_level: str = "low"
    latency_ms: dict[str, float] = Field(default_factory=dict)  # §18.5: SLA monitoring
    viral_velocity: ViralVelocity = Field(default_factory=ViralVelocity)  # §39 analytics
    keyword_affinity: dict[str, float] = Field(default_factory=dict)
    competitor_delta: float = 0.0
    scores: dict[str, float] = Field(default_factory=dict)


class QualityGateMetrics(BaseModel):
    model_config = ConfigDict(extra="allow")
    prompt_actionable: bool = True
    grounded_score: float = 1.0
    novelty_score: float = 1.0
    safety_pass: bool = True
    forbidden_phrases_found: list[str] = Field(default_factory=list)
    issues: list[str] = Field(default_factory=list)
    passed: bool = True
    review_score: float = 0.0
    publish_ready: bool = False
    approved_at: str = ""


class PostResult(BaseModel):
    model_config = ConfigDict(extra="ignore")
    status: str = "pending"
    url: str = ""
    note: str = ""
    updated_at: str = ""
    attempts: int = 0
    failure_reason: str = ""
    browser_profile: str = ""
    screenshots: list[str] = Field(default_factory=list)
    current_step: int = 0
    total_steps: int = 6
    completed_steps: list[str] = Field(default_factory=list)
    next_action: str = ""
    step_details: dict[str, str] = Field(default_factory=dict)


class CompetitorArticle(BaseModel):
    model_config = ConfigDict(extra="ignore")
    url: str
    title: str
    canonical_url: str = ""
    author: str = ""
    published_at: str = ""
    clean_text: str = ""
    headings: list[str] = Field(default_factory=list)
    links: list[str] = Field(default_factory=list)
    summary: str = ""
    raw_html_hash: str = ""
    content_hash: str = ""

    def to_dict(self) -> dict[str, Any]:
        return self.model_dump()


class ComplianceEvent(BaseModel):
    """Ref §11.2: GDPR-compliant audit event."""

    model_config = ConfigDict(extra="ignore")
    event_id: str
    timestamp: str
    operator_id: str
    action: str
    run_id: str = ""
    resource_type: str = "run"
    pii_scrubbed: bool = True
    legal_basis: str = "legitimate_interest"


class TrendSignal(BaseModel):
    model_config = ConfigDict(extra="ignore")
    source: str
    title: str
    url: str
    snippet: str = ""
    published_at: str = ""
    score: int = 0

    def to_dict(self) -> dict[str, Any]:
        return self.model_dump()


class IdentityRecord(BaseModel):
    model_config = ConfigDict(extra="ignore")
    worker_id: str = ""
    persona_id: str = "default"
    canonical_url_hash: str = ""
    content_hash: str = ""


class PipelineStatus(BaseModel):
    model_config = ConfigDict(extra="ignore")
    current_stage: str = "init"
    stages_completed: list[str] = Field(default_factory=list)
    last_error: str = ""


class RunRecord(BaseModel):
    model_config = ConfigDict(extra="ignore")

    run_id: str
    created_at: str
    updated_at: str
    prompt: str
    competitor_url: str
    keywords: list[str]
    platform_targets: list[str]
    status: str = "created"
    approval_state: str = "pending"
    schema_version: str = "v6.1"
    publish_adapter_version: str = "openclaw-browser-v1"
    prompt_bundle_version: str = "prompt_v1"
    retrieval_policy_version: str = "ret_v1"
    scoring_policy_version: str = "score_v1"
    approval_policy: str = "required_if_risk_medium_or_higher"
    trigger_kind: str = "manual"
    confidence: float | None = None
    telegram_route: str = ""
    telegram_preview_sent_at: str = ""
    automation_paused_snapshot: bool = False
    notes: list[str] = Field(default_factory=list)

    article: CompetitorArticle | None = None
    daily_input: DailyInput = Field(default_factory=DailyInput)
    analysis: AnalysisResult = Field(default_factory=AnalysisResult)
    trend_signals: list[TrendSignal] = Field(default_factory=list)
    memory_hits: list[dict[str, Any]] = Field(default_factory=list)
    graph_hits: list[dict[str, Any]] = Field(default_factory=list)
    outputs: dict[str, str] = Field(default_factory=dict)
    post_results: dict[str, PostResult] = Field(default_factory=dict)
    stage_timestamps: dict[str, str] = Field(default_factory=dict)
    quality_gate: QualityGateMetrics = Field(default_factory=QualityGateMetrics)
    cost_proof: dict[str, Any] = Field(default_factory=dict)
    identity: IdentityRecord = Field(default_factory=IdentityRecord)
    proof_pack: dict[str, Any] = Field(default_factory=dict)
    graph_mode: str = "sequential"
    simulation: dict[str, Any] = Field(default_factory=dict)
    pipeline: PipelineStatus = Field(default_factory=PipelineStatus)
    operator_resolution: dict[str, Any] = Field(default_factory=dict)
    failure_taxonomy: list[str] = Field(default_factory=list)

    @field_validator("schema_version")
    @classmethod
    def validate_schema_version(cls, v: str) -> str:
        if v != "v6.1":
            raise ValueError(f"Unsupported schema version: {v}. Expected v6.1")
        return v

    @model_validator(mode="before")
    @classmethod
    def normalize_approval_state(cls, data: Any) -> Any:
        if isinstance(data, dict):
            status = data.get("status", "created")
            approval_state = data.get("approval_state")
            if not approval_state:
                if status in {"approved", "posting", "posted"}:
                    data["approval_state"] = "approved"
                elif status == "rejected":
                    data["approval_state"] = "rejected"
                else:
                    data["approval_state"] = "pending"
        return data

    @model_validator(mode="after")
    def normalize_post_results(self) -> RunRecord:
        for platform in self.platform_targets:
            if platform not in self.post_results:
                self.post_results[platform] = PostResult()
        return self

    def to_dict(self) -> dict[str, Any]:
        return self.model_dump()
