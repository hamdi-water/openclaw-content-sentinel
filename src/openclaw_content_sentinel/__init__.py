from __future__ import annotations

import asyncio
import inspect
import warnings

if getattr(asyncio, "iscoroutinefunction", None) is not inspect.iscoroutinefunction:
    asyncio.iscoroutinefunction = inspect.iscoroutinefunction  # type: ignore[assignment]

warnings.filterwarnings(
    "ignore",
    message=(
        r"'asyncio\.iscoroutinefunction' is deprecated and slated for removal in "
        r"Python 3\.16; use inspect\.iscoroutinefunction\(\) instead"
    ),
    category=DeprecationWarning,
)
warnings.filterwarnings(
    "ignore",
    message=r"Unclosed <MemoryObjectReceiveStream.*>",
    category=ResourceWarning,
)

__all__ = ["__version__"]

__version__ = "0.1.0"
