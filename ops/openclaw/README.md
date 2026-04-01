# OpenClaw Integration Templates

This folder contains repo-side templates for wiring the workspace into a real OpenClaw deployment.

Contents:

- `openclaw.example.jsonc`: gateway config template for plugin loading, Telegram, browser control, and agent defaults
- `bootstrap-local-openclaw.ps1`: local Windows bootstrap for OpenClaw config, plugin load path, and gateway token
- `bootstrap-local-openclaw.sh`: local Linux bootstrap for the same repo-side OpenClaw wiring
- `configure-telegram.ps1`: bind a real Telegram bot token and operator allowlist from environment variables
- `configure-model-and-memory.ps1`: set the primary model and optionally enable local embeddings memory
- `open-social-logins.ps1`: open all three dedicated browser profiles so sandbox accounts can log in manually
- `cron/`: helper scripts and notes for the daily scheduled run
- `browser-profiles.md`: naming and setup guidance for the three social profiles

The templates assume:

- local development on Windows is allowed,
- production runs on Ubuntu,
- Telegram operator approval is mandatory,
- the `plugins/sentinel-ops` plugin is loaded explicitly.

Quick local bootstrap:

```powershell
powershell -ExecutionPolicy Bypass -File .\ops\openclaw\bootstrap-local-openclaw.ps1
```

Telegram setup after you have a real bot token and operator IDs:

```powershell
$env:OPENCLAW_TELEGRAM_BOT_TOKEN = "<bot-token>"
$env:OPENCLAW_TELEGRAM_ALLOWED_USERS = "123456789,987654321"
powershell -ExecutionPolicy Bypass -File .\ops\openclaw\configure-telegram.ps1 -RestartGateway
```

Primary model and memory setup:

```powershell
powershell -ExecutionPolicy Bypass -File .\ops\openclaw\configure-model-and-memory.ps1 -RestartGateway
```

If you already configured the model directly inside OpenClaw, the script keeps the current model and only adjusts memory settings.

If you want to attempt local embeddings:

```powershell
powershell -ExecutionPolicy Bypass -File .\ops\openclaw\configure-model-and-memory.ps1 -EnableLocalMemory -RestartGateway
openclaw memory status --deep
```

Social browser login bootstrap:

```powershell
powershell -ExecutionPolicy Bypass -File .\ops\openclaw\open-social-logins.ps1
```

Monitoring and simulation:

```powershell
python -m openclaw_content_sentinel.cli build-dashboard --json
python -m openclaw_content_sentinel.cli simulate --days 14
```
