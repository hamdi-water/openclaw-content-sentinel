---
name: monitoring-dashboard
description: Build monitoring data and review run health, success rate, and zero-cost proof.
user-invocable: true
---

# Monitoring Dashboard

Use this skill to inspect the operational health of the workspace or after a simulation.

## Commands

Build dashboard data:

```powershell
python -m openclaw_content_sentinel.cli build-dashboard --json
```

Run a simulation:

```powershell
python -m openclaw_content_sentinel.cli simulate --days 14
```

## Expectations

- Use simulation only for dry runs and dashboard validation.
- Do not present simulated posts as live social output.
- Highlight success rate, pending approvals, posting failures, and zero-cost evidence.
