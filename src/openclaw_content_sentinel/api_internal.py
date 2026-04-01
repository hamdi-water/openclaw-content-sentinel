"""Backward-compatible shim for the internal API module."""

from __future__ import annotations

from .api import build_app, setup_routes

__all__ = ["build_app", "setup_routes"]
