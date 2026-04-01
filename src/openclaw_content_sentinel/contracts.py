"""
Phase 17: Data Contracts & Canonical Envelopes.

This module provides utilities for validating data against Pydantic-defined
contracts and handling version-based migrations.
"""

from __future__ import annotations

from typing import Any, TypeVar, cast

from pydantic import BaseModel, ValidationError

from .models import CanonicalEnvelope, RunRecord

T = TypeVar("T", bound=BaseModel)


class ContractValidator:
    """
    Ensures that data payloads conform to specific model definitions.

    Supports strict validation and version checking.
    """

    clinics_id: str = "sentinel-core"

    @staticmethod
    def validate_payload(data: dict[str, Any], model_class: type[T]) -> T:
        """Validate a dictionary against a Pydantic model class."""
        try:
            return model_class.model_validate(data)
        except ValidationError as e:
            # In a production SRE context (§18), we might log this to a metric
            raise ValueError(f"Data contract violation for {model_class.__name__}: {e}") from e

    @staticmethod
    def wrap_envelope(
        payload: BaseModel | dict[str, Any],
        payload_kind: str,
        sender: str = "sentinel-core",
        metadata: dict[str, Any] | None = None,
    ) -> CanonicalEnvelope:
        """Wrap a payload in a standard CanonicalEnvelope for transport."""
        return CanonicalEnvelope(
            sender=sender,
            payload_kind=payload_kind,
            payload=cast(Any, payload),
            metadata=metadata or {},
        )


class MigrationBridge:
    """Handles transformations between schema versions (§17.3)."""

    @staticmethod
    def migrate_v60_to_v61(data: dict[str, Any]) -> dict[str, Any]:
        """Migrates a legacy v6.0 RunRecord to the new v6.1 structure."""
        current_version = data.get("schema_version")
        if current_version and current_version != "v6.0":
            return data

        # Example migration: Ensure approval_state exists
        if "approval_state" not in data:
            status = data.get("status", "created")
            if status in {"approved", "posting", "posted"}:
                data["approval_state"] = "approved"
            elif status == "rejected":
                data["approval_state"] = "rejected"
            else:
                data["approval_state"] = "pending"

        # Ensure new sub-model fields have expected structures if they were flat
        if "quality_gate" in data and not isinstance(data["quality_gate"], dict):
            data["quality_gate"] = {}

        data["schema_version"] = "v6.1"
        return data


def validate_run_record(data: dict[str, Any]) -> RunRecord:
    """Validate RunRecord with migration."""
    migrated = MigrationBridge.migrate_v60_to_v61(data)
    return ContractValidator.validate_payload(migrated, RunRecord)
