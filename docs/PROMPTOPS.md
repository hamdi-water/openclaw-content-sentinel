# PromptOps

The `v6.1` spec requires prompt versioning, quality taxonomy, golden-set tracking, and a release gate for prompt changes.

## Runtime Surface

- CLI: `ocs promptops-report`
- Output:
  - `workspace-data/promptops/promptops-report.json`
  - `workspace-data/promptops/promptops-report.md`

## What It Tracks

- default prompt bundle hash
- prompt-hash registry across runs
- image-prompt hash registry
- quality issue taxonomy
- publish-blocker taxonomy
- approval outcomes
- live golden-set candidate runs
