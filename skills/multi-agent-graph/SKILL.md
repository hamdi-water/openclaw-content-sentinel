---
name: multi-agent-graph
description: Run the LangGraph-backed daily pipeline for research, drafting, review, and memory sync.
user-invocable: true
---

# Multi-Agent Graph

Use this skill when the operator wants the full autonomous content workflow instead of a partial manual sequence.

## Commands

Daily run from explicit inputs:

```powershell
python -m openclaw_content_sentinel.cli daily-run --prompt "<prompt>" --competitor-url "<url>"
```

Daily run from configured daily input:

```powershell
python -m openclaw_content_sentinel.cli scheduled-run
```

## What this graph does

1. create run
2. ingest competitor article
3. collect trend signals
4. retrieve historical memory
5. package prompts and operator artifacts
6. generate article and social drafts
7. run quality gate
8. sync the run back into local vector memory

## Expectations

- Stop if the daily input is missing a competitor URL.
- Do not publish automatically unless the operator explicitly approves and the confidence gate passes.
- Treat the browser posting phase as a separate controlled step.
