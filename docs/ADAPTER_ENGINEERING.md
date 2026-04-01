# Adapter Engineering

The `v6.1` spec treats platform publishers as adapters with stable contracts, fixtures, and replayability.

## Runtime Surface

- CLI: `ocs adapter-manifest`
- CLI: `ocs adapter-unfreeze --platform <linkedin|facebook|x>`
- Output:
  - `workspace-data/adapters/adapter-manifest.json`
  - `workspace-data/adapters/adapter-manifest.md`
  - `workspace-data/adapters/adapter-state.json`
  - `workspace-data/adapters/incidents/<run_id>/<platform>-<timestamp>/fixture-pack.{json,md}`

## Covered Adapters

- LinkedIn
- Facebook
- X

The manifest exposes:

- compose URL
- selector families
- success texts
- last-known browser-health state
- selector registry version
- adapter freeze controls
- recent failure counters
- last fixture-pack path

The runtime now supports:

- a versioned selector registry in `config/selector_registry.json`
- automatic incident fixture packs on publish failures
- adapter freeze after repeated failures inside a bounded window
- operator unfreeze after review
