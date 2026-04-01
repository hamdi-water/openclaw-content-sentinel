---
name: approval-ops
description: Handle approval, rejection, status, and operator summaries for a run.
user-invocable: true
---

# Approval Ops

Use this skill for Telegram-style operator actions such as `/status`, `/approve`, `/reject`, and `/logs`.

## Commands

Workspace status:

```powershell
python -m openclaw_content_sentinel.cli status --json
```

Workspace doctor:

```powershell
python -m openclaw_content_sentinel.cli doctor
```

Detailed run status:

```powershell
python -m openclaw_content_sentinel.cli status --json --run-id "<run_id>"
```

Logs:

```powershell
python -m openclaw_content_sentinel.cli logs --run-id "<run_id>"
```

Approve:

```powershell
python -m openclaw_content_sentinel.cli approve --run-id "<run_id>" --note "<optional note>"
```

Reject:

```powershell
python -m openclaw_content_sentinel.cli reject --run-id "<run_id>" --note "<optional note>"
```

Pause:

```powershell
python -m openclaw_content_sentinel.cli pause --reason "<optional reason>"
```

Resume:

```powershell
python -m openclaw_content_sentinel.cli resume --reason "<optional reason>"
```

## Expectations

- Keep the operator response concise.
- Include the run ID, status, confidence, and next action.
- Do not trigger posting from this skill unless the operator explicitly asks for it.
