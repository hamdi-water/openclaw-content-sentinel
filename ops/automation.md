# OpenClaw Automation Notes

## Recommended cron jobs

- Daily content run:
  - create run
  - ingest competitor source
  - research trends
  - package prompts and draft files
  - send Telegram preview for approval

## Recommended operator commands

- `/ocs-doctor`
- `/ocs-dashboard`
- `/ocs-status`
- `/ocs-run-now`
- `/ocs-approve <run_id>`
- `/ocs-reject <run_id>`
- `/ocs-logs <run_id>`
- `/ocs-pause`
- `/ocs-resume`
- `/ocs-stage-publish <run_id>`
- `/ocs-publish <run_id>`

## Example helper commands

```powershell
python -m openclaw_content_sentinel.cli daily-run --prompt-file .\config\daily_prompt_template.md --competitor-url "https://www.rnz.co.nz/news/national/590645/health-nz-staff-told-to-stop-using-chatgpt-to-write-clinical-notes"
python -m openclaw_content_sentinel.cli status --json
python -m openclaw_content_sentinel.cli build-dashboard --json
python -m openclaw_content_sentinel.cli simulate --days 14
```
