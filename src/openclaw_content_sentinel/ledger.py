from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from .utils import ensure_dir, now_utc

SCHEMA_SQL = """
PRAGMA journal_mode=WAL;
CREATE TABLE IF NOT EXISTS run_ledger (
  ledger_id INTEGER PRIMARY KEY AUTOINCREMENT,
  run_id TEXT NOT NULL,
  recorded_at TEXT NOT NULL,
  event_type TEXT NOT NULL,
  status TEXT NOT NULL,
  approval_state TEXT NOT NULL,
  confidence REAL,
  schema_version TEXT NOT NULL,
  publish_adapter_version TEXT NOT NULL,
  prompt_actionable INTEGER NOT NULL DEFAULT 0,
  proof_eligible INTEGER NOT NULL DEFAULT 0,
  current_step INTEGER NOT NULL DEFAULT 0,
  current_step_title TEXT NOT NULL DEFAULT '',
  live_proof_complete INTEGER NOT NULL DEFAULT 0,
  payload_json TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_run_ledger_run_id ON run_ledger(run_id, ledger_id DESC);
CREATE INDEX IF NOT EXISTS idx_run_ledger_recorded_at ON run_ledger(recorded_at DESC);
CREATE TABLE IF NOT EXISTS cost_ledger (
  cost_id INTEGER PRIMARY KEY AUTOINCREMENT,
  run_id TEXT NOT NULL,
  recorded_at TEXT NOT NULL,
  llm_surface TEXT NOT NULL DEFAULT '',
  image_backend TEXT NOT NULL DEFAULT '',
  free_backends_json TEXT NOT NULL,
  paid_services_json TEXT NOT NULL,
  payload_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS audit_ledger (
  audit_id INTEGER PRIMARY KEY AUTOINCREMENT,
  run_id TEXT NOT NULL,
  recorded_at TEXT NOT NULL,
  actor TEXT NOT NULL,
  action TEXT NOT NULL,
  previous_state TEXT,
  new_state TEXT,
  metadata_json TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_audit_ledger_run_id ON audit_ledger(run_id, audit_id DESC);
"""


def _connect(db_path: Path) -> sqlite3.Connection:
    ensure_dir(db_path.parent)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


def initialize_ledger(db_path: Path) -> Path:
    with closing(_connect(db_path)) as conn:
        conn.executescript(SCHEMA_SQL)
        conn.commit()
    return db_path


def _confidence_value(run: dict[str, Any]) -> float | None:
    try:
        value = run.get("confidence")
        if value is None:
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _pipeline_snapshot(run: dict[str, Any]) -> tuple[int, str]:
    pipeline = (run.get("analysis") or {}).get("delivery_pipeline") or {}
    step = pipeline.get("current_step") or 0
    try:
        step_number = int(step)
    except (TypeError, ValueError):
        step_number = 0
    return step_number, str(pipeline.get("current_step_title") or "")


def _proof_snapshot(run: dict[str, Any]) -> dict[str, Any]:
    proof = (run.get("analysis") or {}).get("proof_readiness") or {}
    quality_gate = run.get("quality_gate") or {}
    metrics = quality_gate.get("metrics") or {}
    return {
        "prompt_actionable": bool(metrics.get("prompt_actionable")),
        "proof_eligible": bool(proof.get("proof_eligible")),
        "live_proof_complete": bool(proof.get("live_proof_complete")),
    }


def _parse_recorded_at(value: str) -> str:
    text = str(value or "").strip()
    return text or now_utc()


def _synthetic_backfill_events(run: dict[str, Any], event_prefix: str) -> list[tuple[str, str]]:
    events: list[tuple[str, str]] = []
    created_at = str(run.get("created_at") or "").strip()
    if created_at:
        events.append((f"{event_prefix}:created", created_at))
    stage_timestamps = run.get("stage_timestamps") or {}
    if isinstance(stage_timestamps, dict):
        for stage, recorded_at in sorted(
            stage_timestamps.items(),
            key=lambda item: (str(item[1] or ""), str(item[0] or "")),
        ):
            timestamp = str(recorded_at or "").strip()
            stage_name = str(stage or "").strip() or "stage"
            if timestamp:
                events.append((f"{event_prefix}:{stage_name}", timestamp))
    updated_at = str(run.get("updated_at") or "").strip()
    if updated_at:
        events.append((f"{event_prefix}:final", updated_at))
    if not events:
        events.append((event_prefix, now_utc()))
    deduped: list[tuple[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for event_type, recorded_at in events:
        key = (event_type, recorded_at)
        if key in seen:
            continue
        seen.add(key)
        deduped.append(key)
    return deduped


def append_run_snapshot(
    db_path: Path,
    run: dict[str, Any],
    event_type: str = "snapshot",
    recorded_at: str = "",
) -> dict[str, Any]:
    initialize_ledger(db_path)
    recorded_at = _parse_recorded_at(recorded_at)
    step_number, step_title = _pipeline_snapshot(run)
    proof = _proof_snapshot(run)
    payload_json = json.dumps(run, ensure_ascii=True)
    with closing(_connect(db_path)) as conn:
        conn.execute(
            """
            INSERT INTO run_ledger (
              run_id, recorded_at, event_type, status, approval_state, confidence,
              schema_version, publish_adapter_version, prompt_actionable, proof_eligible,
              current_step, current_step_title, live_proof_complete, payload_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                str(run.get("run_id") or ""),
                recorded_at,
                event_type,
                str(run.get("status") or ""),
                str(run.get("approval_state") or ""),
                _confidence_value(run),
                str(run.get("schema_version") or ""),
                str(run.get("publish_adapter_version") or ""),
                1 if proof["prompt_actionable"] else 0,
                1 if proof["proof_eligible"] else 0,
                step_number,
                step_title,
                1 if proof["live_proof_complete"] else 0,
                payload_json,
            ),
        )
        cost_proof = run.get("cost_proof") or {}
        conn.execute(
            """
            INSERT INTO cost_ledger (
              run_id, recorded_at, llm_surface, image_backend,
              free_backends_json, paid_services_json, payload_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                str(run.get("run_id") or ""),
                recorded_at,
                str(cost_proof.get("llm_surface") or ""),
                str(cost_proof.get("image_backend") or ""),
                json.dumps(cost_proof.get("free_backends_used") or [], ensure_ascii=True),
                json.dumps(cost_proof.get("paid_services_used") or [], ensure_ascii=True),
                json.dumps(cost_proof, ensure_ascii=True),
            ),
        )
        ledger_id = int(conn.execute("SELECT last_insert_rowid()").fetchone()[0])
        conn.commit()
    return {
        "ledger_db_path": str(db_path),
        "recorded_at": recorded_at,
        "event_type": event_type,
        "ledger_id": ledger_id,
    }


def append_audit_log(
    db_path: Path,
    run_id: str,
    actor: str,
    action: str,
    previous_state: str | None = None,
    new_state: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    initialize_ledger(db_path)
    recorded_at = now_utc()
    metadata_json = json.dumps(metadata or {}, ensure_ascii=True)
    with closing(_connect(db_path)) as conn:
        conn.execute(
            """
            INSERT INTO audit_ledger (
              run_id, recorded_at, actor, action, previous_state, new_state, metadata_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (run_id, recorded_at, actor, action, previous_state, new_state, metadata_json),
        )
        audit_id = int(conn.execute("SELECT last_insert_rowid()").fetchone()[0])
        conn.commit()
    return {
        "audit_id": audit_id,
        "recorded_at": recorded_at,
    }


def fetch_run_history(db_path: Path, run_id: str, limit: int = 25) -> list[dict[str, Any]]:

    initialize_ledger(db_path)
    with closing(_connect(db_path)) as conn:
        rows = conn.execute(
            """
            SELECT ledger_id, run_id, recorded_at, event_type, status, approval_state, confidence,
                   schema_version, publish_adapter_version, prompt_actionable, proof_eligible,
                   current_step, current_step_title, live_proof_complete
            FROM run_ledger
            WHERE run_id = ?
            ORDER BY ledger_id DESC
            LIMIT ?
            """,
            (run_id, max(1, int(limit))),
        ).fetchall()
    return [dict(row) for row in rows]


def ledger_summary(db_path: Path) -> dict[str, Any]:
    initialize_ledger(db_path)
    with closing(_connect(db_path)) as conn:
        run_rows = conn.execute(
            """
            SELECT COUNT(*) AS snapshots,
                   COUNT(DISTINCT run_id) AS distinct_runs,
                   MAX(recorded_at) AS latest_snapshot_at
            FROM run_ledger
            """
        ).fetchone()
        latest_posted = conn.execute(
            """
            SELECT run_id, recorded_at
            FROM run_ledger
            WHERE live_proof_complete = 1
            ORDER BY ledger_id DESC
            LIMIT 1
            """
        ).fetchone()
    return {
        "db_path": str(db_path),
        "snapshots": int((run_rows or {})["snapshots"] if run_rows else 0),
        "distinct_runs": int((run_rows or {})["distinct_runs"] if run_rows else 0),
        "latest_snapshot_at": (run_rows or {})["latest_snapshot_at"] if run_rows else "",
        "latest_live_proof_run_id": latest_posted["run_id"] if latest_posted else "",
        "latest_live_proof_at": latest_posted["recorded_at"] if latest_posted else "",
    }


def calculate_sli_metrics(db_path: Path, days: int = 1) -> dict[str, Any]:
    initialize_ledger(db_path)
    since = (datetime.now() - timedelta(days=days)).isoformat()
    with closing(_connect(db_path)) as conn:
        row = conn.execute(
            """
            SELECT COUNT(DISTINCT run_id) AS total,
                   COUNT(DISTINCT CASE WHEN live_proof_complete = 1 THEN run_id END) AS successes
            FROM run_ledger
            WHERE recorded_at >= ?
            """,
            (since,),
        ).fetchone()
    total = row["total"] or 0
    successes = row["successes"] or 0
    rate = (successes / total) if total > 0 else 1.0
    return {
        "period_days": days,
        "total_runs": total,
        "successful_runs": successes,
        "success_rate": rate,
        "slo_met": rate >= 0.95,
    }


def rebuild_ledger(
    db_path: Path, runs: list[dict[str, Any]], reset: bool = True, event_type: str = "backfill"
) -> dict[str, Any]:

    initialize_ledger(db_path)
    with closing(_connect(db_path)) as conn:
        if reset:
            conn.execute("DELETE FROM run_ledger")
            conn.execute("DELETE FROM cost_ledger")
            conn.commit()
    indexed = 0
    indexed_events = 0
    for run in sorted(
        runs, key=lambda item: (str(item.get("created_at") or ""), str(item.get("run_id") or ""))
    ):
        events = _synthetic_backfill_events(run, event_type)
        for synthetic_event_type, recorded_at in events:
            append_run_snapshot(
                db_path, run, event_type=synthetic_event_type, recorded_at=recorded_at
            )
            indexed_events += 1
        indexed += 1
    summary = ledger_summary(db_path)
    summary["indexed_runs"] = indexed
    summary["indexed_events"] = indexed_events
    summary["reset"] = reset
    return summary


def delete_old_snapshots(db_path: Path, cutoff_at: str) -> None:
    """Ref §29.8: Delete snapshots older than the cutoff timestamp."""
    initialize_ledger(db_path)
    with closing(_connect(db_path)) as conn:
        conn.execute("DELETE FROM run_ledger WHERE recorded_at < ?", (cutoff_at,))
        conn.execute("DELETE FROM cost_ledger WHERE recorded_at < ?", (cutoff_at,))
        conn.execute("DELETE FROM audit_ledger WHERE recorded_at < ?", (cutoff_at,))
        conn.commit()
