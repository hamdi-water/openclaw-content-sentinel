# ADR-001: OpenClaw-First Runtime Architecture

## Status

Accepted

## Context

The assignment requires that OpenClaw remain the primary runtime for:

- scheduling via native cron and heartbeat,
- browser automation with persistent profiles,
- Telegram operator interaction,
- workspace-native skills,
- zero-cost orchestration with minimal external services.

The system also needs deterministic operator controls, daily autonomous runs, human approval gates, and a future path toward LangGraph and local retrieval.

## Decision

OpenClaw Content Sentinel is implemented as an OpenClaw-first workspace where:

- OpenClaw owns the runtime, scheduling, browser control, and native channel integration.
- The local Python package owns deterministic state, artifacts, research collection, drafting, review gates, local vector memory, dashboard generation, and browser publishing helpers.
- Telegram operator actions are exposed through the `sentinel-ops` plugin and backed by the local CLI.
- LangGraph is enabled as a repo-side orchestration layer with sequential fallback when unavailable.
- Retrieval defaults to a local zero-cost vector store and can switch to Chroma or FAISS when those backends are installed.

## Consequences

Positive:

- keeps the architecture aligned with the brief,
- avoids external paid orchestration dependencies,
- centralizes operational control in OpenClaw,
- supports progressive hardening without rewriting the stack.

Tradeoffs:

- live Telegram and social posting still depend on real credentials and authenticated sessions,
- local browser publishing reliability is partially constrained by the host OpenClaw gateway runtime,
- advanced memory backends remain optional until the environment installs the required packages.

## Follow-up

- enable the real Telegram channel with operator allowlist,
- connect sandbox social profiles,
- validate browser publishing live,
- run the 14-day reliability test and collect the 30-day zero-cost log.
