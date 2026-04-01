"""Prefer the workspace `src/` package tree over any installed copy."""

from __future__ import annotations

import asyncio
import inspect
import sys
import warnings
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SRC = ROOT / "src"

if SRC.exists():
    src_text = str(SRC)
    sys.path[:] = [src_text, *[path for path in sys.path if path != src_text]]

if getattr(asyncio, "iscoroutinefunction", None) is not inspect.iscoroutinefunction:
    asyncio.iscoroutinefunction = inspect.iscoroutinefunction  # type: ignore[assignment]

# Python 3.14 currently surfaces third-party deprecations from FastAPI/Starlette/Chroma
# that are outside this workspace and would otherwise fail `-W error` runs.
warnings.filterwarnings(
    "ignore",
    message=r"'asyncio\.iscoroutinefunction' is deprecated and slated for removal in Python 3\.16; use inspect\.iscoroutinefunction\(\) instead",
    category=DeprecationWarning,
)
warnings.filterwarnings(
    "ignore",
    message=r"Unclosed <MemoryObjectReceiveStream.*>",
    category=ResourceWarning,
)
