from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from .config import AppConfig
from .contracts import validate_run_record
from .ledger import append_run_snapshot, initialize_ledger, ledger_summary, rebuild_ledger
from .logging_utils import get_logger
from .models import RunRecord
from .utils import dump_json, ensure_dir, load_json, now_utc, write_text

logger = get_logger(__name__)

DEFAULT_FAILURE_TAXONOMY = [
    "not_logged_in",
    "challenge_page",
    "composer_missing",
    "submit_failed",
    "confirmation_missing",
    "rate_limited",
]


class RunStore:
    def __init__(self, config: AppConfig, project_id: str | None = None) -> None:
        self.config = config
        self.project_id = project_id or config.project_id

    @property
    def project_dir(self) -> Path:
        return self.config.base_dir / "projects" / self.project_id

    @property
    def runs_dir(self) -> Path:
        if self.project_id == self.config.project_id:
            return self.config.runs_dir
        return self.project_dir / "runs"

    def bootstrap(self) -> None:
        ensure_dir(self.config.data_dir)
        ensure_dir(self.project_dir)
        ensure_dir(self.runs_dir)
        ensure_dir(self.config.logs_dir)
        initialize_ledger(self.config.ledger_db_path)
        if (
            ledger_summary(self.config.ledger_db_path).get("snapshots", 0) == 0
            and self.config.runs_dir.exists()
        ):
            runs: list[dict[str, Any]] = []
            for path in sorted(self.config.runs_dir.glob("*/run.json")):
                payload = load_json(path)
                if payload:
                    runs.append(self.normalize_run(payload))
            if runs:
                rebuild_ledger(
                    self.config.ledger_db_path, runs, reset=True, event_type="bootstrap_backfill"
                )
        if not self.control_json().exists():
            self.save_control(self.default_control())

    def run_dir(self, run_id: str) -> Path:
        return self.runs_dir / run_id

    def run_json(self, run_id: str) -> Path:
        return self.run_dir(run_id) / "run.json"

    def control_json(self) -> Path:
        return self.config.data_dir / "control.json"

    def default_control(self) -> dict[str, Any]:
        return {
            "automation_paused": False,
            "reason": "",
            "updated_at": now_utc(),
            "updated_by": "system",
        }

    def load_control(self) -> dict[str, Any]:
        payload = load_json(self.control_json(), default=self.default_control())
        return {
            **self.default_control(),
            **(payload or {}),
        }

    def save_control(self, control: dict[str, Any]) -> Path:
        payload = {
            **self.default_control(),
            **control,
        }
        path = self.control_json()
        dump_json(path, payload)
        return path

    def load_run(self, run_id: str) -> dict[str, Any]:
        payload = load_json(self.run_json(run_id))
        if not payload:
            raise FileNotFoundError(f"Unknown run_id: {run_id}")
        return self.normalize_run(payload)

    def save_run(self, run: RunRecord | dict[str, Any]) -> Path:
        payload = run.to_dict() if isinstance(run, RunRecord) else run
        payload = self.normalize_run(payload)
        payload = self._scrub_pii(payload)
        path = self.run_json(payload["run_id"])
        dump_json(path, payload)
        append_run_snapshot(
            self.config.ledger_db_path,
            payload,
            event_type=f"save:{payload.get('status', 'unknown')}",
        )
        return path

    def _scrub_pii(self, data: Any) -> Any:
        if isinstance(data, dict):
            return {k: self._scrub_pii(v) for k, v in data.items() if not self._is_sensitive_key(k)}
        if isinstance(data, list):
            return [self._scrub_pii(item) for item in data]
        if isinstance(data, str):
            # Redact email-like patterns and potential tokens
            text = re.sub(
                r"[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+", "[EMAIL_REDACTED]", data
            )
            # Simple heuristic for potential API keys/tokens (long alphanumeric strings in values)
            if len(text) > 32 and text.isalnum() and not any(c.isspace() for c in text):
                return "[TOKEN_REDACTED]"
            return text
        return data

    def _is_sensitive_key(self, key: str) -> bool:
        k = str(key).lower()
        if k == "key_call_to_action":
            return False
        return any(s in k for s in ("api_key", "secret", "password", "token", "auth", "credential"))

    def save_text_artifact(self, run_id: str, relative_path: str, content: str) -> Path:
        path = self.run_dir(run_id) / relative_path
        write_text(path, content)
        return path

    def list_runs(self, limit: int | None = None) -> list[dict[str, Any]]:
        runs: list[dict[str, Any]] = []
        if not self.runs_dir.exists():
            return runs
        for path in sorted(self.runs_dir.glob("*/run.json"), reverse=True):
            payload = load_json(path)
            if payload:
                runs.append(self.normalize_run(payload))
            if limit is not None and len(runs) >= limit:
                break
        return runs

    def list_artifacts(self, run_id: str) -> list[str]:
        root = self.run_dir(run_id)
        if not root.exists():
            return []
        return [
            str(path.relative_to(root)).replace("\\", "/")
            for path in sorted(root.rglob("*"))
            if path.is_file()
        ]

    def normalize_run(self, run: dict[str, Any]) -> dict[str, Any]:
        """Enforce Phase 17 data contracts and version migrations."""
        return validate_run_record(run).model_dump()


class PurgeService:
    """Ref §29.7: Garbage collection for old runs and artifacts."""

    def __init__(self, config: AppConfig) -> None:
        self.config = config
        self.store = RunStore(config)

    def purge_old_runs(self, retention_days: int = 90) -> dict[str, Any]:
        """Delete runs older than retention_days."""
        cutoff = datetime.now(UTC) - timedelta(days=retention_days)
        runs = self.store.list_runs()
        purged_count = 0

        for run in runs:
            created_at_str = run.get("created_at") or ""
            if not created_at_str:
                continue
            try:
                created_at = datetime.fromisoformat(created_at_str.replace("Z", "+00:00"))
                if created_at < cutoff:
                    run_id = run["run_id"]
                    run_dir = self.store.run_dir(run_id)
                    if run_dir.exists():
                        import shutil
                        shutil.rmtree(run_dir)
                        purged_count += 1
            except Exception as exc:
                logger.warning(f"Failed to purge run `{run_id}`: {exc}")
                continue

        # §29.8: Clean up stale entries in the SQL ledger
        from .ledger import delete_old_snapshots
        delete_old_snapshots(self.config.ledger_db_path, cutoff.isoformat())

        return {"purged_count": purged_count, "cutoff": cutoff.isoformat(), "ledger_cleaned": True}
