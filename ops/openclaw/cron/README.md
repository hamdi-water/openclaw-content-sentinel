# Daily Cron Setup

Recommended schedule:

- cron expression: `0 8 * * *`
- timezone: `Africa/Tunis`
- target agent: the Content Sentinel workspace agent

Recommended agent message:

`Run the OpenClaw Content Sentinel daily workflow. Use the scheduled-run command if config/daily_input.json is present. If no competitor URL is configured, stop and request operator input. Draft all required artifacts, mark the Telegram preview sent when Telegram is configured, and never publish without approval.`
