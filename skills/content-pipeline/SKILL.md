---
name: content-pipeline
description: Package the run for drafting, generate the content set, and review the publish gate.
user-invocable: true
---

# Content Pipeline

Use this skill when a run has been ingested and researched and is ready for drafting.

## Commands

```powershell
python -m openclaw_content_sentinel.cli package --run-id "<run_id>"
python -m openclaw_content_sentinel.cli generate-drafts --run-id "<run_id>"
python -m openclaw_content_sentinel.cli review-run --run-id "<run_id>"
```

## Required outputs

Write into the run folder:

- `drafts/article.md`
- `drafts/linkedin.md`
- `drafts/facebook.md`
- `drafts/x.md`
- `drafts/image_prompt.md`
- `ops/review_report.md`

## Drafting rules

- Use `prompts/openclaw_generation_brief.md`.
- Keep the output original and on-brand.
- Attribute the competitor source where appropriate.
- Use trend evidence selectively.
- Use historical memory context when it improves consistency.
- Do not mark the run as approved. Approval is handled separately.
