# Sentinel Ops Plugin

This plugin adds deterministic OpenClaw commands for the Content Sentinel workspace.

Native commands:

- `/security`
- `/compliance`
- `/runtime`
- `/scheduler`
- `/browser-health [platform]`
- `/status [run_id]`
- `/logs <run_id>`
- `/run-now [competitor_url]`
- `/approve <run_id> [note]`
- `/reject <run_id> [note]`
- `/pause [reason]`
- `/resume [reason]`
- `/stage-publish <run_id> [platform]`
- `/publish <run_id> [platform]`
- `/retry <run_id> <platform>`

Legacy aliases with the `ocs-` prefix remain available.

When `/run-now` is called without a URL, it consumes the prepared daily input from `config/daily_input.json` through the `scheduled-run` CLI command.

`/approve` approves the run and immediately launches the automatic browser posting flow.

`/publish` executes the browser automation flow. Use `/stage-publish` if you only want the prepared metadata without opening the browser composer.

`/compliance` surfaces the current cahier des charges gap analysis directly from the workspace CLI.

`/browser-health` is the fastest way to confirm whether the social browser profiles are logged in before a live publish.
