# v6.1 Deep Scan

This document records the latest deep scan against `Cahier_des_charges_OpenClaw_Content_Sentinel_v6_1_ultra_advanced_revised.pdf`.

## Figure Crosswalk

- Figure 1 / 24: target architecture and reference planes  
  Code mapping: `workflow.py`, `storage.py`, `ledger.py`, `memory.py`, `graph_store.py`, `api.py`
- Figure 2 / v3-1: Telegram cockpit and human approval loop  
  Code mapping: `plugins/sentinel-ops/index.js`, `telegram_notify.py`, `operations.py`
- Figure 3 / v3-2: zero-cost research and scraping ladder  
  Code mapping: `competitor.py`, `research.py`
- Figure 4: daily business sequence  
  Code mapping: `workflow.py`, `langgraph_workflow.py`
- Figure 5: publish, verification, recovery  
  Code mapping: `browser_automation.py`, `workflow.py`
- Figure 6 / v3-3: internal API type GenViral  
  Code mapping: `api.py`, `contracts/internal-api-v1.yaml`
- Figure 7 / v3-4: testing pyramid and 14/30-day proof  
  Code mapping: `tests/test_workflow.py`, `simulation.py`, `compliance.py`, `gap_tracker.py`
- Figures 21 / 26 / 31: RAG and RAGOps  
  Code mapping: `memory.py`, `graph_store.py`, `ragops.py`
- Figure 23 / 27: OpenClaw control plane and LangGraph workflow plane  
  Code mapping: `workflow.py`, `langgraph_workflow.py`
- Figure 29: PromptOps loop  
  Code mapping: `promptops.py`, `editorial.py`, `drafting.py`
- Figure 30: release train  
  Code mapping: `release_ops.py`, `docs/RELEASE_TRAIN.md`
- Figure 32: social adapter contract  
  Code mapping: `browser_automation.py`, `adapter_ops.py`

## Code-Side Gaps Closed

- SQLite run ledger as canonical truth plane
- schema versioning and publish adapter version attached to runs
- internal API contracts and same-repo facade
- GenViral-like control-plane services for campaigns, templates, render jobs, analytics snapshots, and events
- weighted v6.1 gap tracker on 100
- separate development-vs-proof scoring in the gap tracker
- PromptOps reporting
- RAGOps reporting
- adapter engineering manifest
- selector registry with versioned browser selectors
- incident fixture packs for publish failures
- adapter freeze controls and operator unfreeze flow
- release readiness reporting

## Remaining Non-Code Gaps

- 14-day live reliability proof is below target
- 30-day eligible live history is still too short
- demo video is still a manual deliverable
- remote GitHub delivery cannot be proven from local files alone
