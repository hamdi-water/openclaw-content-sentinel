# Security and Hardening

## Controls implemented

- approval is required before publishing,
- browser profiles are isolated per platform,
- Telegram is designed for allowlist-only operator control,
- recent runs, screenshots, and publish failures are tracked per platform,
- local artifacts and `.env` are protected by `.gitignore`,
- `ocs security-audit` checks the local runtime posture.

## Run the audit

```powershell
python -m openclaw_content_sentinel.cli security-audit
```

## Expected production posture

- Telegram enabled with `dmPolicy=allowlist`
- operator IDs restricted to named maintainers
- browser profiles logged into sandbox or production accounts intentionally
- no secrets committed to the workspace
- `workspace-data/` excluded from version control
- reverse proxy trust configured if the OpenClaw UI is exposed beyond loopback

## Remaining live dependencies

- real Telegram token
- operator allowlist IDs
- authenticated sandbox or production browser sessions
- production host hardening outside this repo
