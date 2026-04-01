---
name: publisher-facebook
description: Publish the approved Facebook draft through the OpenClaw browser profile.
user-invocable: true
---

# Facebook Publisher

Use this skill only after the run is approved.

## Preconditions

- `drafts/facebook.md` exists and has been reviewed.
- `ops/review_report.md` says the run is publish-ready.
- The operator approved the run.
- The dedicated Facebook browser profile is logged in.

## Browser procedure

1. Execute the live browser publish:

```powershell
python -m openclaw_content_sentinel.cli browser-publish --run-id "<run_id>" --platform facebook
```

2. If you want a non-destructive rehearsal first, run:

```powershell
python -m openclaw_content_sentinel.cli browser-publish --run-id "<run_id>" --platform facebook --no-submit
```

3. The automation opens the dedicated browser profile, navigates to the compose surface, types `drafts/facebook.md`, attempts the image upload, captures screenshots, and persists the final result in `run.json`.

4. Use `python -m openclaw_content_sentinel.cli publish --run-id "<run_id>" --platform facebook` only when you want metadata staging without browser execution.
