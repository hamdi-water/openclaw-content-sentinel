from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"

sys.path[:] = [str(SRC), *[path for path in sys.path if path != str(SRC)]]
