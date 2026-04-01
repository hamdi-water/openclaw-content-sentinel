"""
Phase 21: SRE Plane — Automated Ledger Recovery & State Reconstruction.

========================================================================

This module provides utilities to reconstruct the SQLite ledger from the
authoritative filesystem 'run.json' records in case of database corruption
or data loss.
"""

from __future__ import annotations

import logging
from typing import Any

from .config import AppConfig
from .ledger import ledger_summary, rebuild_ledger
from .logging_utils import get_logger
from .storage import RunStore


class LedgerReconstructor:
    """Manages the reconstruction of the SRE ledger from filesystem artifacts."""

    def __init__(self, config: AppConfig) -> None:
        self.config = config
        self.store = RunStore(config)
        self.log = get_logger(__name__)

    def audit_and_repair(self, force: bool = False) -> dict[str, Any]:
        """
        Compare ledger summary with filesystem records and repair if inconsistent.

        Returns a report of the actions taken.
        """
        self.log.info("Starting ledger consistency audit...")

        db_path = self.config.ledger_db_path
        summary = ledger_summary(db_path)

        # Count physical runs on disk
        runs = self.store.list_runs()
        physical_count = len(runs)
        stored_count = summary.get("distinct_runs", 0)

        self.log.info(f"Audit: physical_runs={physical_count}, ledger_runs={stored_count}")

        report: dict[str, Any] = {
            "physical_runs": physical_count,
            "ledger_runs": stored_count,
            "repaired": False,
            "actions": [],
        }

        if force or physical_count > stored_count:
            self.log.warning("Inconsistency detected or force requested. Rebuilding ledger...")
            rebuild_report = rebuild_ledger(
                db_path, runs, reset=True, event_type="recovery_rebuild"
            )
            report["repaired"] = True
            report["actions"].append("rebuild_ledger_from_disk")
            report["rebuild_details"] = rebuild_report
            self.log.info("Ledger successfully reconstructed.")
        else:
            self.log.info("Ledger is consistent with disk state.")

        return report


def auto_recover(config: AppConfig) -> None:
    """Run automated recovery on boot."""
    reconstructor = LedgerReconstructor(config)
    try:
        reconstructor.audit_and_repair()
    except Exception as exc:
        logging.error(f"Automated recovery failed: {exc}")
