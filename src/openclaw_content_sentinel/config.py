from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


def _load_dotenv(root: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    path = root / ".env"
    if not path.exists():
        return values
    for raw_line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if not key:
            continue
        if value and len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        values[key] = value
    return values


def _env(name: str, default: str | None, values: dict[str, str]) -> str | None:
    if name in os.environ:
        return os.environ[name]
    if name in values:
        return values[name]
    return default


def _bool_env(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _bool_env_with_values(name: str, default: bool, values: dict[str, str]) -> bool:
    value = _env(name, None, values)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class AppConfig:
    base_dir: Path
    data_dir: Path
    runs_dir: Path
    logs_dir: Path
    ledger_db_path: Path
    brand_profile_file: Path
    feeds_file: Path
    daily_input_file: Path
    daily_brief_queue_file: Path
    source_registry_file: Path
    selector_registry_file: Path
    schema_version: str
    publish_adapter_version: str
    selector_registry_version: str
    prompt_bundle_version: str
    retrieval_policy_version: str
    scoring_policy_version: str
    approval_policy: str
    retention_days_proofs: int
    searx_url: str | None
    comfyui_url: str | None
    comfyui_workflow_file: Path
    approval_required: bool
    default_targets: tuple[str, ...]
    default_prompt_file: Path
    telegram_target: str | None
    telegram_bot_token: str | None
    preferred_model: str | None
    operator_timezone: str
    enable_pytrends: bool
    confidence_threshold: float
    competitor_overlap_threshold: float
    memory_overlap_threshold: float
    auto_publish_enabled: bool
    image_backend: str
    image_seed: int
    image_width: int
    image_height: int
    publish_url_linkedin: str
    publish_url_facebook: str
    publish_url_x: str
    memory_backend: str
    memory_collection: str
    memory_chroma_dir: Path
    memory_faiss_dir: Path
    graph_backend: str
    graph_local_path: Path
    neo4j_uri: str | None
    neo4j_username: str | None
    neo4j_password: str | None
    neo4j_database: str
    browser_profile_linkedin: str
    browser_profile_facebook: str
    browser_profile_x: str
    browser_profile_scraper: str
    openclaw_bin: str
    browser_timeout_ms: int
    browser_publish_retries: int
    browser_retry_wait_ms: int
    browser_page_settle_ms: int
    browser_modal_settle_ms: int
    browser_input_settle_ms: int
    browser_upload_settle_ms: int
    browser_submit_wait_ms: int
    browser_submit_wait_ms_facebook: int
    browser_health_cache_seconds: int
    adapter_failure_threshold: int
    adapter_failure_window_minutes: int
    adapter_freeze_minutes: int
    enable_public_news: bool
    user_agent: str
    http_timeout: int
    api_internal_token: str
    latency_targets: dict[str, int]  # §27.2: SLA targets in ms
    worker_id: str = ""
    project_id: str = "default"  # §32: Multi-Project isolation

    @property
    def src_dir(self) -> Path:
        return self.base_dir / "src"

    @classmethod
    def from_env(cls, base_dir: Path | None = None) -> AppConfig:
        root = base_dir or Path.cwd()
        dotenv_values = _load_dotenv(root)
        data_dir = root / (
            _env("OPENCLAW_SENTINEL_DATA_DIR", "workspace-data", dotenv_values) or "workspace-data"
        )
        runs_dir = root / (
            _env("OPENCLAW_SENTINEL_RUNS_DIR", str(data_dir / "runs"), dotenv_values)
            or str(data_dir / "runs")
        )
        logs_dir = root / (
            _env("OPENCLAW_SENTINEL_LOGS_DIR", str(data_dir / "logs"), dotenv_values)
            or str(data_dir / "logs")
        )
        ledger_db_path = root / (
            _env(
                "OPENCLAW_SENTINEL_LEDGER_DB_PATH",
                str(data_dir / "ledger" / "run_ledger.sqlite3"),
                dotenv_values,
            )
            or str(data_dir / "ledger" / "run_ledger.sqlite3")
        )
        brand_profile_file = root / (
            _env("OPENCLAW_SENTINEL_BRAND_PROFILE_FILE", "config/brand_profile.md", dotenv_values)
            or "config/brand_profile.md"
        )
        feeds_file = root / (
            _env("OPENCLAW_SENTINEL_FEEDS_FILE", "config/default_feeds.json", dotenv_values)
            or "config/default_feeds.json"
        )
        daily_input_file = root / (
            _env("OPENCLAW_SENTINEL_DAILY_INPUT_FILE", "config/daily_input.json", dotenv_values)
            or "config/daily_input.json"
        )
        daily_brief_queue_file = root / (
            _env(
                "OPENCLAW_SENTINEL_DAILY_BRIEF_QUEUE_FILE",
                "config/daily_brief_queue.json",
                dotenv_values,
            )
            or "config/daily_brief_queue.json"
        )
        source_registry_file = root / (
            _env(
                "OPENCLAW_SENTINEL_SOURCE_REGISTRY_FILE",
                "config/source_registry.json",
                dotenv_values,
            )
            or "config/source_registry.json"
        )
        selector_registry_file = root / (
            _env(
                "OPENCLAW_SENTINEL_SELECTOR_REGISTRY_FILE",
                "config/selector_registry.json",
                dotenv_values,
            )
            or "config/selector_registry.json"
        )
        default_prompt_file = root / (
            _env(
                "OPENCLAW_SENTINEL_DEFAULT_PROMPT_FILE",
                "config/daily_prompt_template.md",
                dotenv_values,
            )
            or "config/daily_prompt_template.md"
        )
        searx_url = _env("OPENCLAW_SENTINEL_SEARX_URL", None, dotenv_values) or None
        comfyui_url = _env("OPENCLAW_SENTINEL_COMFYUI_URL", None, dotenv_values) or None
        comfyui_workflow_file = root / (
            _env(
                "OPENCLAW_SENTINEL_COMFYUI_WORKFLOW_FILE",
                "config/comfyui_workflow_template.json",
                dotenv_values,
            )
            or "config/comfyui_workflow_template.json"
        )
        telegram_target = _env("OPENCLAW_SENTINEL_TELEGRAM_TARGET", None, dotenv_values) or None
        telegram_bot_token = _env("OPENCLAW_TELEGRAM_BOT_TOKEN", None, dotenv_values) or None
        preferred_model = _env("OPENCLAW_SENTINEL_MODEL", None, dotenv_values) or None
        default_targets = tuple(
            item.strip()
            for item in (
                _env("OPENCLAW_SENTINEL_DEFAULT_TARGETS", "linkedin,facebook,x", dotenv_values)
                or "linkedin,facebook,x"
            ).split(",")
            if item.strip()
        )
        worker_id_file = data_dir / ".sentinel_id"
        if worker_id_file.exists():
            worker_id = worker_id_file.read_text(encoding="utf-8").strip()
        else:
            import uuid

            worker_id = str(uuid.uuid4())
            if not data_dir.exists():
                data_dir.mkdir(parents=True, exist_ok=True)
            worker_id_file.write_text(worker_id, encoding="utf-8")

        return cls(
            base_dir=root,
            data_dir=data_dir,
            runs_dir=runs_dir,
            logs_dir=logs_dir,
            ledger_db_path=ledger_db_path,
            brand_profile_file=brand_profile_file,
            feeds_file=feeds_file,
            daily_input_file=daily_input_file,
            daily_brief_queue_file=daily_brief_queue_file,
            source_registry_file=source_registry_file,
            selector_registry_file=selector_registry_file,
            schema_version=_env("OPENCLAW_SENTINEL_SCHEMA_VERSION", "v6.1", dotenv_values)
            or "v6.1",
            publish_adapter_version=(
                _env(
                    "OPENCLAW_SENTINEL_PUBLISH_ADAPTER_VERSION",
                    "openclaw-browser-v1",
                    dotenv_values,
                )
                or "openclaw-browser-v1"
            ),
            selector_registry_version=_env(
                "OPENCLAW_SENTINEL_SELECTOR_REGISTRY_VERSION",
                "selector-registry-v1",
                dotenv_values,
            )
            or "selector-registry-v1",
            prompt_bundle_version=_env(
                "OPENCLAW_SENTINEL_PROMPT_BUNDLE_VERSION", "prompt_v1", dotenv_values
            )
            or "prompt_v1",
            retrieval_policy_version=_env(
                "OPENCLAW_SENTINEL_RETRIEVAL_POLICY_VERSION", "ret_v1", dotenv_values
            )
            or "ret_v1",
            scoring_policy_version=_env(
                "OPENCLAW_SENTINEL_SCORING_POLICY_VERSION", "score_v1", dotenv_values
            )
            or "score_v1",
            approval_policy=_env(
                "OPENCLAW_SENTINEL_APPROVAL_POLICY",
                "required_if_risk_medium_or_higher",
                dotenv_values,
            )
            or "required_if_risk_medium_or_higher",
            retention_days_proofs=int(
                _env("OPENCLAW_SENTINEL_RETENTION_DAYS_PROOFS", "180", dotenv_values) or "180"
            ),
            default_prompt_file=default_prompt_file,
            searx_url=searx_url,
            comfyui_url=comfyui_url,
            comfyui_workflow_file=comfyui_workflow_file,
            telegram_target=telegram_target,
            telegram_bot_token=telegram_bot_token,
            preferred_model=preferred_model,
            operator_timezone=_env("OPENCLAW_SENTINEL_TIMEZONE", "Africa/Tunis", dotenv_values)
            or "Africa/Tunis",
            enable_pytrends=_bool_env_with_values(
                "OPENCLAW_SENTINEL_ENABLE_PYTRENDS", True, dotenv_values
            ),
            confidence_threshold=float(
                _env("OPENCLAW_SENTINEL_CONFIDENCE_THRESHOLD", "0.75", dotenv_values) or "0.75"
            ),
            competitor_overlap_threshold=float(
                _env("OPENCLAW_SENTINEL_COMPETITOR_OVERLAP_THRESHOLD", "0.55", dotenv_values)
                or "0.55"
            ),
            memory_overlap_threshold=float(
                _env("OPENCLAW_SENTINEL_MEMORY_OVERLAP_THRESHOLD", "0.68", dotenv_values) or "0.68"
            ),
            auto_publish_enabled=_bool_env_with_values(
                "OPENCLAW_SENTINEL_AUTO_PUBLISH", False, dotenv_values
            ),
            image_backend=(
                (
                    _env("OPENCLAW_SENTINEL_IMAGE_BACKEND", "procedural", dotenv_values)
                    or "procedural"
                )
                .strip()
                .lower()
                or "procedural"
            ),
            image_seed=int(_env("OPENCLAW_SENTINEL_IMAGE_SEED", "42", dotenv_values) or "42"),
            image_width=int(_env("OPENCLAW_SENTINEL_IMAGE_WIDTH", "1200", dotenv_values) or "1200"),
            image_height=int(_env("OPENCLAW_SENTINEL_IMAGE_HEIGHT", "630", dotenv_values) or "630"),
            publish_url_linkedin=(
                _env(
                    "OPENCLAW_SENTINEL_PUBLISH_URL_LINKEDIN",
                    "https://www.linkedin.com/preload/sharebox/",
                    dotenv_values,
                )
                or "https://www.linkedin.com/preload/sharebox/"
            ),
            publish_url_facebook=(
                _env(
                    "OPENCLAW_SENTINEL_PUBLISH_URL_FACEBOOK",
                    "https://www.facebook.com/",
                    dotenv_values,
                )
                or "https://www.facebook.com/"
            ),
            publish_url_x=(
                _env("OPENCLAW_SENTINEL_PUBLISH_URL_X", "https://x.com/compose/post", dotenv_values)
                or "https://x.com/compose/post"
            ),
            memory_backend=(
                (_env("OPENCLAW_SENTINEL_MEMORY_BACKEND", "local", dotenv_values) or "local")
                .strip()
                .lower()
                or "local"
            ),
            memory_collection=_env(
                "OPENCLAW_SENTINEL_MEMORY_COLLECTION", "content_sentinel_runs", dotenv_values
            )
            or "content_sentinel_runs",
            memory_chroma_dir=root
            / (
                _env(
                    "OPENCLAW_SENTINEL_MEMORY_CHROMA_DIR",
                    str(data_dir / "memory" / "chroma"),
                    dotenv_values,
                )
                or str(data_dir / "memory" / "chroma")
            ),
            memory_faiss_dir=root
            / (
                _env(
                    "OPENCLAW_SENTINEL_MEMORY_FAISS_DIR",
                    str(data_dir / "memory" / "faiss"),
                    dotenv_values,
                )
                or str(data_dir / "memory" / "faiss")
            ),
            graph_backend=(
                (_env("OPENCLAW_SENTINEL_GRAPH_BACKEND", "local", dotenv_values) or "local")
                .strip()
                .lower()
                or "local"
            ),
            graph_local_path=root
            / (
                _env(
                    "OPENCLAW_SENTINEL_GRAPH_LOCAL_PATH",
                    str(data_dir / "graph" / "knowledge_graph.json"),
                    dotenv_values,
                )
                or str(data_dir / "graph" / "knowledge_graph.json")
            ),
            neo4j_uri=_env("OPENCLAW_SENTINEL_NEO4J_URI", None, dotenv_values) or None,
            neo4j_username=_env("OPENCLAW_SENTINEL_NEO4J_USERNAME", None, dotenv_values) or None,
            neo4j_password=_env("OPENCLAW_SENTINEL_NEO4J_PASSWORD", None, dotenv_values) or None,
            neo4j_database=_env("OPENCLAW_SENTINEL_NEO4J_DATABASE", "neo4j", dotenv_values)
            or "neo4j",
            approval_required=_bool_env_with_values(
                "OPENCLAW_SENTINEL_APPROVAL_REQUIRED", True, dotenv_values
            ),
            default_targets=default_targets,
            browser_profile_linkedin=(
                _env(
                    "OPENCLAW_SENTINEL_BROWSER_PROFILE_LINKEDIN", "openclaw-linkedin", dotenv_values
                )
                or "openclaw-linkedin"
            ),
            browser_profile_facebook=(
                _env(
                    "OPENCLAW_SENTINEL_BROWSER_PROFILE_FACEBOOK", "openclaw-facebook", dotenv_values
                )
                or "openclaw-facebook"
            ),
            browser_profile_x=(
                _env("OPENCLAW_SENTINEL_BROWSER_PROFILE_X", "openclaw-x", dotenv_values)
                or "openclaw-x"
            ),
            browser_profile_scraper=(
                _env(
                    "OPENCLAW_SENTINEL_BROWSER_PROFILE_SCRAPER", "openclaw-research", dotenv_values
                )
                or "openclaw-research"
            ),
            openclaw_bin=_env("OPENCLAW_SENTINEL_OPENCLAW_BIN", "openclaw", dotenv_values)
            or "openclaw",
            browser_timeout_ms=int(
                _env("OPENCLAW_SENTINEL_BROWSER_TIMEOUT_MS", "45000", dotenv_values) or "45000"
            ),
            browser_publish_retries=int(
                _env("OPENCLAW_SENTINEL_BROWSER_PUBLISH_RETRIES", "1", dotenv_values) or "1"
            ),
            browser_retry_wait_ms=int(
                _env("OPENCLAW_SENTINEL_BROWSER_RETRY_WAIT_MS", "500", dotenv_values) or "500"
            ),
            browser_page_settle_ms=int(
                _env("OPENCLAW_SENTINEL_BROWSER_PAGE_SETTLE_MS", "350", dotenv_values) or "350"
            ),
            browser_modal_settle_ms=int(
                _env("OPENCLAW_SENTINEL_BROWSER_MODAL_SETTLE_MS", "250", dotenv_values) or "250"
            ),
            browser_input_settle_ms=int(
                _env("OPENCLAW_SENTINEL_BROWSER_INPUT_SETTLE_MS", "180", dotenv_values) or "180"
            ),
            browser_upload_settle_ms=int(
                _env("OPENCLAW_SENTINEL_BROWSER_UPLOAD_SETTLE_MS", "650", dotenv_values) or "650"
            ),
            browser_submit_wait_ms=int(
                _env("OPENCLAW_SENTINEL_BROWSER_SUBMIT_WAIT_MS", "900", dotenv_values) or "900"
            ),
            browser_submit_wait_ms_facebook=int(
                _env("OPENCLAW_SENTINEL_BROWSER_SUBMIT_WAIT_MS_FACEBOOK", "1400", dotenv_values)
                or "1400"
            ),
            browser_health_cache_seconds=int(
                _env("OPENCLAW_SENTINEL_BROWSER_HEALTH_CACHE_SECONDS", "600", dotenv_values)
                or "600"
            ),
            adapter_failure_threshold=int(
                _env("OPENCLAW_SENTINEL_ADAPTER_FAILURE_THRESHOLD", "3", dotenv_values) or "3"
            ),
            adapter_failure_window_minutes=int(
                _env("OPENCLAW_SENTINEL_ADAPTER_FAILURE_WINDOW_MINUTES", "240", dotenv_values)
                or "240"
            ),
            adapter_freeze_minutes=int(
                _env("OPENCLAW_SENTINEL_ADAPTER_FREEZE_MINUTES", "240", dotenv_values)
                or "240"
            ),
            enable_public_news=_bool_env_with_values(
                "OPENCLAW_SENTINEL_ENABLE_PUBLIC_NEWS", True, dotenv_values
            ),
            user_agent=(
                _env(
                    "OPENCLAW_SENTINEL_USER_AGENT",
                    "OpenClawContentSentinel/0.1 (+self-hosted)",
                    dotenv_values,
                )
                or "OpenClawContentSentinel/0.1 (+self-hosted)"
            ),
            http_timeout=int(_env("OPENCLAW_SENTINEL_HTTP_TIMEOUT", "15", dotenv_values) or "15"),
            api_internal_token=_env(
                "OPENCLAW_SENTINEL_API_INTERNAL_TOKEN", "default-insecure-token", dotenv_values
            )
            or "default-insecure-token",
            latency_targets={
                "drafting": 30000,
                "research": 45000,
                "acquisition": 20000,
                "total": 120000,
            },
            worker_id=worker_id,
        )
