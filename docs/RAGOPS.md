# RAGOps

The `v6.1` spec requires index lifecycle visibility, shadow evaluation, and grounded retrieval observability.

## Runtime Surface

- CLI: `ocs ragops-report`
- Output:
  - `workspace-data/ragops/ragops-report.json`
  - `workspace-data/ragops/ragops-report.md`

## What It Tracks

- active memory backend
- active graph backend
- index manifest for vector and graph stores
- basic shadow-eval queries derived from live proof-eligible runs
- retrieval hit counts across memory and graph layers
