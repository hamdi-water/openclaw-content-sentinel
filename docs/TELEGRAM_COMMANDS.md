# Telegram Command Reference

Recommended native Sentinel commands:

- `/security`: audit local security posture and runtime hardening
- `/compliance`: build a cahier des charges compliance report
- `/runtime`: inspect active OpenClaw runtime state for Sentinel
- `/scheduler`: audit cron, stale approvals, and missing previews
- `/queue`: show the operator action queue for prompt gaps, stale approvals, and failed publishes
- `/briefs`: inspect the queued daily briefs available for scheduled runs
- `/promote-brief <brief_id>`: promote one queued brief into the active `daily_input.json`
- `/browser-health [platform]`: verify browser profiles before live posting
- `/doctor`: check workspace readiness and critical setup state
- `/dashboard`: show monitoring summary and recent run health
- `/status [run_id]`: show global automation state or one run
- `/run-now <competitor_url>`: trigger a new run from the configured prompt file
- `/approve <run_id> [note]`: approve the prepared run, then launch automatic posting
- `/reject <run_id> [note]`: reject the prepared run and require edits
- `/logs <run_id>`: show artifacts and current state
- `/pause [reason]`: temporarily stop scheduled automation
- `/resume [reason]`: resume the automation
- `/stage-publish <run_id> [platform]`: prepare publish metadata for all or one platform
- `/publish <run_id> [platform]`: execute live browser publishing for all or one platform
- `/retry <run_id> <platform>`: retry a failed live browser publish

Legacy aliases with the `ocs-` prefix are still available for backward compatibility.

OpenClaw built-ins can still coexist:

- `/status`: built-in OpenClaw status
- `/help`: built-in command help

Sentinel routing:

- status and approvals -> `approval-ops` plus the `sentinel-ops` plugin
- doctor -> `approval-ops` plus the `sentinel-ops` plugin
- dashboard -> `monitoring-dashboard` plus the `sentinel-ops` plugin
- run-now -> `daily-run` plus the `sentinel-ops` plugin
- publishing -> `publisher-linkedin`, `publisher-facebook`, `publisher-x`
