from __future__ import annotations

from typing import Any


class OCSError(Exception):
    """Base exception for all OpenClaw Content Sentinel errors."""

    def __init__(self, message: str, metadata: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.metadata = metadata or {}


class AcquisitionError(OCSError):
    """Raised when source discovery or ingestion fails."""


class RAGError(OCSError):
    """Raised when vector store or grounding logic fails."""


class PromptError(OCSError):
    """Raised when prompt rendering or LLM invocation fails."""


class PostingError(OCSError):
    """Raised when browser automation or social publishing fails."""


class ComplianceError(OCSError):
    """Raised when PII scrubbing or audit logging fails."""


class SLAError(OCSError):
    """Raised when processing exceeds the defined latency budget."""
