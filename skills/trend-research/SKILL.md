---
name: trend-research
description: Gather zero-cost trend signals from RSS, pytrends, and optional SearXNG.
user-invocable: true
---

# Trend Research

Use this skill after competitor ingestion or when the operator asks for a trend refresh.

## Command

```powershell
python -m openclaw_content_sentinel.cli research --run-id "<run_id>"
```

## Expectations

- Prefer the trend signals already captured in the run folder.
- Use pytrends results when available to confirm whether a keyword has rising or related demand.
- Use browser or web search only to validate or deepen ambiguous signals.
- Review `artifacts/trend_summary.md` and incorporate only relevant context.
