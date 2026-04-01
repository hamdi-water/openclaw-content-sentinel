# Deployment Guide

## 1. Install the workspace

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e .
```

Recommended production runtime: Python `3.12` or `3.13`. The current Python `3.14` environment works here but emits LangChain/LangGraph compatibility warnings.

## 2. Prepare configuration

- Copy `.env.example` to `.env`
- Adjust feed file, SearXNG URL, optional ComfyUI URL, storage paths, model selection, and `config/daily_input.json`
- If you want the advanced graph layer, set `OPENCLAW_SENTINEL_GRAPH_BACKEND=local` for the default local graph or configure the optional Neo4j values in `.env`

## 3. Optional support services

```powershell
docker compose up -d searxng chroma
```

## 4. OpenClaw integration

- Use this repo as your OpenClaw workspace
- Run `powershell -ExecutionPolicy Bypass -File .\ops\openclaw\bootstrap-local-openclaw.ps1`
- Refresh skills
- Load `plugins/sentinel-ops` if you are not using the bootstrap script
- Apply the template in `ops/openclaw/openclaw.example.jsonc` for production hardening
- Configure the real Telegram bot and operator allowlist:

```powershell
$env:OPENCLAW_TELEGRAM_BOT_TOKEN = "<bot-token>"
$env:OPENCLAW_TELEGRAM_ALLOWED_USERS = "123456789"
powershell -ExecutionPolicy Bypass -File .\ops\openclaw\configure-telegram.ps1 -RestartGateway
```

- For zero-install testing, you can use Groq with a free-plan key:
  `GROQ_API_KEY=<key>` and `OPENCLAW_SENTINEL_MODEL=groq/llama-3.3-70b-versatile`
- If you want to use xAI Grok as the LLM brain, set `XAI_API_KEY` and `OPENCLAW_SENTINEL_MODEL=xai/grok-4` in `.env`.
- This is compatible with the cahier des charges because the LLM is the only paid component allowed. Keep research and posting on free/open-source paths.

- Configure cron and heartbeat in OpenClaw
- Open and log into dedicated browser profiles for LinkedIn, Facebook, and X:

```powershell
powershell -ExecutionPolicy Bypass -File .\ops\openclaw\open-social-logins.ps1
```

- Set the primary OpenClaw model and memory mode:

```powershell
powershell -ExecutionPolicy Bypass -File .\ops\openclaw\configure-model-and-memory.ps1 -RestartGateway
```

- If you want local embeddings later, rerun with `-EnableLocalMemory` and verify with `openclaw memory status --deep`
- `ocs` loads `.env` automatically from the workspace root, so you do not need to manually export every `OPENCLAW_SENTINEL_*` variable before using the CLI
- If you want a true local generative image backend, set `OPENCLAW_SENTINEL_IMAGE_BACKEND=comfyui_local`, verify `OPENCLAW_SENTINEL_COMFYUI_URL`, and adapt `config/comfyui_workflow_template.json`
- For the advanced knowledge graph, use `python -m openclaw_content_sentinel.cli graph-status` and `python -m openclaw_content_sentinel.cli reindex-graph` after enabling a backend

## 5. First supervised run

```powershell
ocs bootstrap
ocs doctor
ocs set-daily-input --prompt-file .\config\daily_prompt_template.md --competitor-url "https://www.rnz.co.nz/news/national/590645/health-nz-staff-told-to-stop-using-chatgpt-to-write-clinical-notes"
ocs scheduled-run
```

## 6. Deterministic command layer

Once the plugin is loaded, expose these Telegram commands:

- `/security`
- `/compliance`
- `/runtime`
- `/scheduler`
- `/queue`
- `/browser-health`
- `/doctor`
- `/dashboard`
- `/status`
- `/run-now`
- `/approve`
- `/reject`
- `/logs`
- `/pause`
- `/resume`
- `/stage-publish`
- `/publish`
- `/retry`

## 7. Visual operator guide

Open `docs/app-guide.html` in a browser for the crystal-style onboarding page and Zod validators.

## 8. Monitoring and handover

```powershell
python -m openclaw_content_sentinel.cli build-dashboard --json
python -m openclaw_content_sentinel.cli compliance-report
python -m openclaw_content_sentinel.cli zero-cost-report --days 30
python -m openclaw_content_sentinel.cli runtime-status
python -m openclaw_content_sentinel.cli scheduler-health
python -m openclaw_content_sentinel.cli browser-health
python -m openclaw_content_sentinel.cli graph-status
python -m openclaw_content_sentinel.cli reindex-graph
python -m openclaw_content_sentinel.cli simulate --days 14
python -m openclaw_content_sentinel.cli security-audit
```

- Dashboard UI: `docs/monitoring-dashboard.html`
- Handover notes: `docs/HANDOVER.md`
- Architecture decision record: `docs/ADR-001-openclaw-first-architecture.md`
- Security guide: `docs/SECURITY.md`
- Generated compliance reports: `workspace-data/compliance/`
