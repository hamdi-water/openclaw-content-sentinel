# OpenClaw Content Sentinel: Platinum Runbook

## Incident Management (§28)

### P0 Alerts: Critical System Incident
- **Symptoms**: Automated Telegram alert with 🚨 prefix. Consecutive publication failures.
- **Action**: 
  1. Check `ocs status` via CLI.
  2. Review `workspace-data/logs/` for the latest trace.
  3. Pause the sentinel: `ocs pause`.
  4. Verify browser credentials.

### P1 Alerts: Persistent Failure
- **Symptoms**: ⚠️ Warning alert. SLI success rate < 95%.
- **Action**:
  1. Check Searx/ComfyUI connectivity.
  2. Verify SQLite ledger integrity: `ocs verify-platinum`.

## Recovery Procedures
- **Rebuild Ledger**: `ocs rebuild-ledger` (backfills from run JSON files).
- **Reset RAG Index**: Delete `workspace-data/memory/` and run `ocs ingest`.
