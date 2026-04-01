$message = "Run the OpenClaw Content Sentinel daily workflow. Use the scheduled-run command if config/daily_input.json is present. If no competitor URL is configured, stop and request operator input. Draft all required artifacts, mark the Telegram preview sent when Telegram is configured, and never publish without approval."

openclaw cron add `
  --name "content-sentinel-daily" `
  --cron "0 8 * * *" `
  --tz "Africa/Tunis" `
  --agent "main" `
  --no-deliver `
  --light-context `
  --message $message
