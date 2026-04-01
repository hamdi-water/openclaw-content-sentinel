# Release Train

The `v6.1` spec defines a release gate around schema stability, browser proof, sandbox validation, and long-horizon reliability.

## Runtime Surface

- CLI: `ocs release-readiness`
- Output:
  - `workspace-data/release/release-readiness.json`
  - `workspace-data/release/release-readiness.md`

## Inputs

- compliance report
- v6.1 weighted gap tracker
- scheduler health
- security audit

## Current Rule

- `go` only when the weighted gap is high enough and no blocker remains
- otherwise `hold`
