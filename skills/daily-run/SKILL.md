---
name: daily-run
description: Create and package the daily content run for OpenClaw Content Sentinel.
user-invocable: true
---

# Daily Run

Use this skill when the operator asks to run today's content job or when cron triggers the daily workflow.

## Steps

1. Collect or confirm:
   - prompt text or prompt file,
   - competitor URL, or a prepared `config/daily_input.json`,
   - optional keywords,
   - optional platform targets.
2. Execute:

```powershell
python -m openclaw_content_sentinel.cli daily-run --prompt "<prompt>" --competitor-url "<url>"
```

If the scheduler or operator wants to use the prepared daily input file instead of inline arguments, execute:

```powershell
python -m openclaw_content_sentinel.cli scheduled-run
```

3. Read the generated run folder under `workspace-data/runs/<run_id>/`.
4. Confirm the graph generated:
   - `drafts/article.md`
   - `drafts/linkedin.md`
   - `drafts/facebook.md`
   - `drafts/x.md`
   - `drafts/image_prompt.md`
   - `ops/review_report.md`
5. Review the generated outputs against `prompts/openclaw_review_checklist.md`.
6. Present the operator summary from `ops/telegram_preview.md`.
7. After the Telegram preview is sent, run:

```powershell
python -m openclaw_content_sentinel.cli mark-preview-sent --run-id "<run_id>" --telegram-route "<telegram_route>"
```

8. Do not publish unless approval is explicit and the review gate says `publish_ready: true`.
