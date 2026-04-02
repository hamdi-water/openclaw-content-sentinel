# OpenClaw Content Sentinel — Project Summary

## What It Is

An autonomous, daily content pipeline that monitors competitor articles, researches trends, generates original multi-platform drafts (LinkedIn, Facebook, X), enforces human approval, and publishes via browser automation. Built as an OpenClaw workspace with a local Python backend (50 modules, ~15k+ LOC).

## Architecture

```
OpenClaw (LLM runtime)
    ↕ Skills layer (10 skills)
Local Python package
    ├── CLI (80+ commands)
    ├── FastAPI REST API
    ├── LangGraph multi-node workflow
    ├── Hybrid RAG (BM25 + vector, RRF fusion)
    ├── Vector memory (local / Chroma / FAISS)
    ├── Knowledge graph (local JSON / Neo4j)
    ├── Browser automation (Playwright)
    ├── Prometheus monitoring
    └── SQLite audit ledger
```

Daily flow: ingest competitor → trend research → hybrid context enrichment → draft article + social posts → generate image → Telegram preview → operator approval → browser publish → post verification.

## What's Good

1. **Clean separation of concerns** — OpenClaw handles LLM orchestration, the Python package handles everything deterministic. Skills bridge the two without coupling.

2. **Pluggable backends everywhere** — Memory (local/Chroma/FAISS), graph (local JSON/Neo4j), images (procedural/ComfyUI), LLM providers (Groq/xAI). Graceful fallbacks when optional deps are missing.

3. **Strong data contracts** — Pydantic v2 models for every data structure, schema versioning (v6.1), migration bridge from v6.0, validation on every load/save cycle.

4. **Production-grade observability** — Prometheus metrics, SRE incident tracking, latency histograms, scraping error counters, file-based metric export for scraping.

5. **Security-first design** — PII scrubbing on all persisted data, secret scanning across the workspace, `.gitignore` enforcement checks, compliance event logging, operator allowlists.

6. **Operator-centric workflow** — Approval gates, confidence thresholds, Telegram previews, pause/resume controls, operator action queue with prioritization. The system never publishes without explicit human sign-off (unless deliberately configured otherwise).

7. **Comprehensive audit trail** — SQLite ledger captures every run state transition. Zero-cost proof documents that research used only free sources. Compliance reports map to the cahier des charges.

8. **Resilient browser automation** — Configurable retries, adapter freezing after repeated failures, health caching, selector registry for platform-specific overrides, screenshot-based verification.

9. **Rich CLI** — 80+ commands covering the full lifecycle: bootstrap, create, ingest, research, draft, review, approve, publish, simulate, audit, dashboard. Good for both interactive use and cron automation.

10. **Simulation mode** — Can dry-run multi-day scenarios without touching real platforms. Useful for validating pipeline changes before production.

## What Can Be Improved

### Code Quality

1. **`workflow.py` is too large** — This single file orchestrates the entire pipeline and likely exceeds 4000 lines. It should be decomposed into focused modules (e.g., `pipeline_ingest.py`, `pipeline_research.py`, `pipeline_drafting.py`, `pipeline_publish.py`).

2. **Import ordering issues** — 26 E402 violations in `langgraph_workflow.py` and `ragops.py` (imports not at top of file). The `warnings.catch_warnings()` workaround in `langgraph_workflow.py` is understandable but should be documented or restructured.

3. **Broad exception handling** — Many `except Exception` blocks silently swallow errors (especially in research, memory, and graph modules). These should be narrowed to specific exception types or at minimum log the error before continuing.

4. **Heavy use of `dict[str, Any]`** — While Pydantic models exist, much of the internal pipeline passes raw dicts. More consistent use of typed models would catch bugs earlier and improve IDE support.

5. **McCabe complexity** — Some functions exceed the configured max complexity of 15. The `ruff: noqa: C901` suppression in `workflow.py` suggests this is known but unaddressed.

### Testing

6. **Low test coverage** — Only 5 test files for 50 source modules. Critical paths like browser automation, CLI commands, storage, security, and the full drafting pipeline have no dedicated tests.

7. **No property-based tests** — The data contract layer (Pydantic models, migrations, envelope wrapping) is a natural fit for property-based testing with Hypothesis but has none.

8. **No test for the CLI entry points** — The CLI has 80+ commands but no test exercises them, even at the argument-parsing level.

### Architecture

9. **`config.py` is a monolith** — `AppConfig` has 80+ fields loaded from environment variables with repetitive `_env()` calls. Consider grouping into sub-configs (BrowserConfig, MemoryConfig, GraphConfig, etc.) and using Pydantic Settings for automatic env loading.

10. **No dependency injection** — Modules import global singletons (`monitor`, config via `AppConfig.from_env()`). This makes testing harder and creates hidden coupling. A lightweight DI container or explicit config passing would help.

11. **Sync/async inconsistency** — Browser automation has both sync and async paths. The LangGraph workflow is async-capable but most of the pipeline runs synchronously. Settling on one model would reduce complexity.

12. **No rate limiting on the API** — The FastAPI layer exposes run creation and publishing endpoints but has no rate limiting or request throttling beyond the internal token check.

### Operations

13. **No CI/CD configuration** — No GitHub Actions, GitLab CI, or similar. Linting, type checking, and tests aren't enforced on push.

14. **Docker Compose is minimal** — Only SearXNG and Chroma. The Python app itself has no Dockerfile, making deployment less reproducible.

15. **PowerShell-centric quick start** — The README examples use PowerShell (`.ps1`), which limits accessibility for Linux/macOS operators. Bash equivalents exist in `ops/` but aren't the default.

16. **No structured logging** — The project uses a custom `get_logger()` but outputs plain text. Structured JSON logging would integrate better with log aggregation tools.

### Documentation

17. **Missing docstrings** — D100–D107 rules are globally suppressed. Most public functions and classes lack docstrings, making the codebase harder to onboard into.

18. **No architecture diagram** — The README describes the layout but a visual diagram of the pipeline stages, data flow, and external integrations would help new contributors.

19. **Scattered section references** — Code comments reference sections like "§17.3", "§30.1", "§29.7" from what appears to be an internal specification document, but that document isn't in the repo. These references are opaque without context.

## Summary Stats

| Metric | Value |
|---|---|
| Source modules | 50 |
| Test files | 5 |
| CLI commands | 80+ |
| Skills | 10 |
| Pydantic models | 15+ |
| Env variables | 60+ |
| Optional dependency groups | 6 (html, research, graph, vector, publishers, api) |
| Python version | ≥ 3.12 |
| Schema version | v6.1 |


## Recommended Model Strategy

Use Groq's free tier with Llama 3.3 70B for all LLM tasks. The free tier provides ~14,400 requests/day and ~6,000 tokens/minute — far more than this pipeline needs (a single daily run uses roughly 10 requests and 15-20k tokens). This covers article drafting, social post generation, editorial review, French content, and hybrid context synthesis at zero cost. Set `OPENCLAW_SENTINEL_MODEL=groq/llama-3.3-70b-versatile` in `.env` and no further model configuration is needed.


## Security Audit

### Critical

1. **CORS misconfiguration** — `api.py` sets `allow_origins=["*"]` with `allow_credentials=True`. Any website you visit while the API server is running can make authenticated requests to it. Fix: restrict to specific trusted origins.

2. **Insecure default API token** — `config.py` defaults `api_internal_token` to `"default-insecure-token"`. No authentication middleware enforces it on API routes. Anyone on your network can hit the API. Fix: require explicit token, add auth middleware.

3. **API key exposure** — `.env` contains plaintext API keys. The file is gitignored but visible to anyone with workspace access. Rotate keys if the workspace was ever shared or committed.

### High

4. **XXE vulnerability in XML parsing** — `research.py` uses `ElementTree.fromstring()` on RSS feed content fetched from the internet. A malicious RSS feed could exploit XML External Entity attacks to read local files or probe your network. Fix: replace with `defusedxml.ElementTree`.

5. **Incomplete SSRF protection** — `fetch_url_with_retry()` in `utils.py` has URL validation but doesn't block private IP ranges (127.0.0.1, 192.168.x.x, 10.x.x.x, 169.254.x.x). A crafted feed URL could make your machine scan your local network or hit cloud metadata endpoints. Fix: add RFC 1918 private range blocking.

6. **Subprocess argument injection** — `browser_automation.py` and `operations.py` use `subprocess.run()` with `shell=False` (good), but arguments come from user-controlled data (run records, platform names). If input validation is bypassed, arguments could be manipulated. Fix: validate all subprocess arguments against allowlists.

### Medium

7. **Path traversal in asset serving** — The API's asset endpoint checks path containment but could be bypassed via symlinks. Fix: use `Path.resolve()` with strict containment and reject `..` segments.

8. **Regex-based HTML sanitization** — `sanitize_html()` in `research.py` uses regex to strip HTML tags. This is fragile and bypassable. Malicious scraped content could inject into LLM prompts. Fix: use `bleach` or `html2text`.

9. **JavaScript injection in browser automation** — `JSSnippets` in `browser_automation.py` uses `{payload}` template substitution with user-controlled text. Special characters in draft content could break out of the JS string context. Fix: use proper JSON serialization for all payloads.

10. **No rate limiting on API** — The FastAPI server has no request throttling. A single client can flood it. Fix: add rate limiting middleware.

### What's Already Good

- `shell=False` on all subprocess calls — no shell injection
- No `eval()`, `exec()`, or `pickle` — no code execution risk
- Security headers present (CSP, X-Frame-Options, HSTS, X-Content-Type-Options)
- PII scrubbing on persisted data
- `.env` and `workspace-data/` are gitignored
- Secret scanning exists (though regex-based and could be stronger)
- Audit logging on API requests with request_id and actor tracking

### Immediate Actions


1. Do not expose the FastAPI server to the internet without fixing CORS and adding auth
2. Install `defusedxml` and replace `ElementTree.fromstring()` calls
3. Add private IP range blocking to `validate_url_safety()`
4. The CLI-only workflow (no API server) is reasonably safe for local use
