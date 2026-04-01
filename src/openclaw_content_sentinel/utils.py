from __future__ import annotations

import json
import random
import re
import shutil
import time
import urllib.request
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse

if TYPE_CHECKING:
    from .config import AppConfig

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",  # noqa: E501
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",  # noqa: E501
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",  # noqa: E501
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:109.0) Gecko/20100101 Firefox/121.0",
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_2 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.2 Mobile/15E148 Safari/604.1",  # noqa: E501
]


def get_random_user_agent() -> str:
    return random.choice(USER_AGENTS)  # noqa: S311


def now_utc() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat()


def slugify(value: str, limit: int = 48) -> str:
    slug = re.sub(r"[^a-zA-Z0-9]+", "-", value.strip().lower()).strip("-")
    return slug[:limit] or "run"


def ensure_dir(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    return path


def read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def write_text(path: Path, content: str) -> None:
    ensure_dir(path.parent)
    path.write_text(content, encoding="utf-8")


def load_json(path: Path, default: Any = None) -> Any:
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def dump_json(path: Path, payload: Any) -> None:
    ensure_dir(path.parent)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=True), encoding="utf-8")


SCRAPING_DOMAIN_ALLOWLIST = {
    "news.google.com",
    "trends.google.com",
    "rss.nytimes.com",
    "feeds.bbci.co.uk",
    "techcrunch.com",
    "venturebeat.com",
    "wired.com",
    "theverge.com",
    "medium.com",
    "reddit.com",
    "rnz.co.nz",
}


def validate_url_safety(url: str) -> bool:
    """Ref §11.2: SSRF protection via domain allowlist."""
    try:
        parsed = urlparse(url)
        if parsed.scheme not in ("http", "https"):
            return False
        domain = parsed.netloc.lower().split(":")[0]
        # In a real environment, we would also block local networks (127.0.0.1, 192.168.x.x)
        return any(
            domain == trusted or domain.endswith("." + trusted)
            for trusted in SCRAPING_DOMAIN_ALLOWLIST
        )
    except Exception:
        return False


def fetch_url_with_retry(
    url: str,
    timeout: int = 20,
    max_retries: int = 3,
    initial_delay: float = 1.0,
    enforce_ssrf_protection: bool = True,
    worker_id: str | None = None,
) -> bytes:
    """Fetch a URL with exponential backoff on 403/429 errors and User-Agent rotation."""
    if enforce_ssrf_protection and not validate_url_safety(url):
        msg = f"SSRF Protection: Blocked domain {urlparse(url).netloc}"
        raise ValueError(msg)

    last_error = None

    for attempt in range(max_retries):
        headers = {
            "User-Agent": get_random_user_agent(),
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.5",
        }
        if worker_id:
            headers["X-Sentinel-ID"] = worker_id
        request = urllib.request.Request(url, headers=headers)  # noqa: S310
        # noqa: S310
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310
                return cast(bytes, response.read())
        except Exception as exc:
            msg = f"HTTP request failed {url}"
            _error: Any = exc  # Resolve assignment type
            last_error = _error
            if isinstance(exc, HTTPError) and exc.code in (403, 429):
                delay = initial_delay * (2**attempt) + random.uniform(0, 1)  # noqa: S311
                time.sleep(delay)
                continue
            elif isinstance(exc, (URLError, Exception)):
                delay = initial_delay * (2**attempt)
                time.sleep(delay)
                continue
            raise
            delay = initial_delay * (2**attempt)
            time.sleep(delay)
            continue

    if last_error:
        raise last_error
    raise RuntimeError(f"Failed to fetch {url} after {max_retries} attempts.")


def cleanup_temp_profiles(config: AppConfig) -> None:
    """Ref §30.5: Purge temporary browser profiles."""
    temp_dir = config.data_dir / "temp_profiles"
    if temp_dir.exists():
        shutil.rmtree(temp_dir, ignore_errors=True)
    temp_dir.mkdir(parents=True, exist_ok=True)


def detect_language(text: str) -> str:
    """Ref §7: Heuristic-based language detection."""
    if not text:
        return "unknown"
    lowered = text.lower()
    # Simple stop-word frequency check
    scores = {
        "fr": len(re.findall(r"\b(le|la|les|un|une|des|est|sont|et|ou|pour)\b", lowered)),
        "en": len(re.findall(r"\b(the|a|an|is|are|and|or|for|to|in|of)\b", lowered)),
        "es": len(re.findall(r"\b(el|la|los|las|un|una|uno|es|son|y|o|para)\b", lowered)),
        "de": len(re.findall(r"\b(der|die|das|und|ist|sind|oder|fuer|mit)\b", lowered)),
    }
    top_lang = max(scores, key=lambda k: cast(int, scores[k]))
    return top_lang if scores[top_lang] > 0 else "en"


class LatencyTracker:
    """Ref §18.5: High-resolution latency tracking for SLA monitoring."""

    def __init__(self) -> None:
        self.starts: dict[str, float] = {}
        self.measurements: dict[str, float] = {}

    def start(self, key: str) -> None:
        self.starts[key] = time.perf_counter()

    def stop(self, key: str) -> float:
        if key in self.starts:
            elapsed = (time.perf_counter() - self.starts.pop(key)) * 1000
            self.measurements[key] = round(elapsed, 2)
            return self.measurements[key]
        return 0.0

    def get_report(self) -> dict[str, float]:
        return self.measurements


class CircuitBreaker:
    """Ref §27.11: Prevent cascading failures for flaky nodes."""

    def __init__(self, failure_threshold: int = 3, reset_timeout: int = 60) -> None:
        self.failure_threshold = failure_threshold
        self.reset_timeout = reset_timeout
        self.failures = 0
        self.last_failure_time = 0.0
        self.state = "CLOSED"

    def record_failure(self) -> None:
        self.failures += 1
        self.last_failure_time = time.time()
        if self.failures >= self.failure_threshold:
            self.state = "OPEN"

    def record_success(self) -> None:
        self.failures = 0
        self.state = "CLOSED"

    def can_execute(self) -> bool:
        if self.state == "OPEN":
            if time.time() - self.last_failure_time > self.reset_timeout:
                self.state = "HALF_OPEN"
                return True
            return False
        return True


class AdaptiveTimeout:
    """Ref §27.13: Adjust timeouts based on remaining error budget."""

    def __init__(self, total_budget_ms: float = 120000.0) -> None:
        self.total_budget_ms = total_budget_ms
        self.start_time = time.perf_counter()

    def get_remaining_timeout_ms(self, default_ms: float) -> float:
        elapsed_ms = (time.perf_counter() - self.start_time) * 1000
        remaining_ms = max(100.0, self.total_budget_ms - elapsed_ms)
        return min(default_ms, remaining_ms)
