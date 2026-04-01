# Handover

## What ships in this workspace

- OpenClaw-first workspace with custom `SKILL.md` skills
- deterministic operator plugin for Telegram-style commands
- LangGraph-backed daily workflow orchestration with sequential fallback
- zero-cost research stack: RSS, pytrends, optional SearXNG
- local vector memory for historical run retrieval, with optional Chroma and FAISS backends
- topic and competitor knowledge graph with a local backend by default and optional Neo4j backend
- local procedural image generation and optional ComfyUI path
- monitoring dashboard data builder and simulation tooling
- compliance and zero-cost evidence reporting

## Critical entry points

- CLI: `python -m openclaw_content_sentinel.cli`
- OpenClaw plugin: `plugins/sentinel-ops`
- Scheduler prompt: `ops/openclaw/cron/setup-daily-cron.ps1`
- Heartbeat: `HEARTBEAT.md`
- Deployment guide: `docs/DEPLOYMENT.md`
- Telegram commands: `docs/TELEGRAM_COMMANDS.md`
- Security guide: `docs/SECURITY.md`
- ADR: `docs/ADR-001-openclaw-first-architecture.md`
- Operator guide: `docs/app-guide.html`
- Monitoring dashboard: `docs/monitoring-dashboard.html`

## Standard daily run

1. Update `config/daily_input.json`
2. Run `ocs doctor`
3. Run `ocs scheduled-run`
4. Let the app send the Telegram preview automatically
5. Approve or reject in Telegram with `/approve <run_id>` or `/reject <run_id>`
6. Let OpenClaw launch browser auto-posting after approval, or use `/publish <run_id>` as an override
7. Record post results with `ocs record-post` only if manual correction is needed
8. Run `ocs build-dashboard --json`
9. Run `ocs compliance-report`
10. Run `ocs zero-cost-report --days 30`
11. Run `ocs reindex-memory` and `ocs reindex-graph` after structural config changes

## Simulation workflow

Use this when you need synthetic data for monitoring and dry-run validation:

```powershell
python -m openclaw_content_sentinel.cli simulate --days 14
python -m openclaw_content_sentinel.cli build-dashboard --json
```

Simulation posts use `simulated://` URLs and must not be presented as live social publishes.

## Operational risks to watch

- prefer Python 3.12 or 3.13 in production because LangChain Core currently warns on Python 3.14
- Telegram token missing or revoked
- browser profiles logged out or challenged
- pytrends noise on overly generic prompts
- SearXNG unavailable
- confidence gate bypass attempts
- manual posting evidence missing from run folder

## External blockers not solved by code alone

- real Telegram bot token and operator allowlist
- authenticated sandbox sessions for LinkedIn, Facebook, and X
- real 14-day live run proof
- real 30-day zero-cost log evidence
- demo video capture and publication proof
