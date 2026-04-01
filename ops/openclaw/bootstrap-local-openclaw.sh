#!/usr/bin/env bash
set -euo pipefail

workspace_dir="${1:-$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)}"
plugin_dir="${PLUGIN_DIR:-$workspace_dir/plugins/sentinel-ops}"
python_path="${PYTHON_PATH:-python3}"
cli_module="${CLI_MODULE:-openclaw_content_sentinel.cli}"
prompt_file="${PROMPT_FILE:-$workspace_dir/config/daily_prompt_template.md}"
batch_path="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/config-set.batch.json"

if [[ ! -d "$plugin_dir" ]]; then
  echo "Plugin directory not found: $plugin_dir" >&2
  exit 1
fi

if [[ ! -f "$prompt_file" ]]; then
  echo "Prompt file not found: $prompt_file" >&2
  exit 1
fi

gateway_token="$(python3 - <<'PY'
import json
import os
import subprocess
import uuid

config_path = subprocess.check_output(["openclaw", "config", "file"], text=True).strip()
token = ""
if os.path.exists(config_path):
    try:
        with open(config_path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        token = (((data.get("gateway") or {}).get("auth") or {}).get("token")) or ""
    except Exception:
        token = ""
print(token or str(uuid.uuid4()))
PY
)"

python3 - "$workspace_dir" "$plugin_dir" "$python_path" "$cli_module" "$prompt_file" "$gateway_token" "$batch_path" <<'PY'
import json
import sys

workspace_dir, plugin_dir, python_path, cli_module, prompt_file, gateway_token, batch_path = sys.argv[1:]
ops = [
    {"path": "agents.defaults.workspace", "value": workspace_dir},
    {"path": "agents.defaults.repoRoot", "value": workspace_dir},
    {"path": "gateway.mode", "value": "local"},
    {"path": "gateway.auth.token", "value": gateway_token},
    {"path": "browser.profiles.openclaw-linkedin.cdpPort", "value": 18810},
    {"path": "browser.profiles.openclaw-linkedin.color", "value": "#0A66C2"},
    {"path": "browser.profiles.openclaw-facebook.cdpPort", "value": 18811},
    {"path": "browser.profiles.openclaw-facebook.color", "value": "#1877F2"},
    {"path": "browser.profiles.openclaw-x.cdpPort", "value": 18812},
    {"path": "browser.profiles.openclaw-x.color", "value": "#111111"},
    {"path": "plugins.load.paths", "value": [plugin_dir]},
    {"path": "plugins.entries.sentinel-ops.enabled", "value": True},
    {"path": "plugins.entries.sentinel-ops.config.workspaceDir", "value": workspace_dir},
    {"path": "plugins.entries.sentinel-ops.config.pythonPath", "value": python_path},
    {"path": "plugins.entries.sentinel-ops.config.cliModule", "value": cli_module},
    {"path": "plugins.entries.sentinel-ops.config.promptFile", "value": prompt_file},
]
with open(batch_path, "w", encoding="utf-8") as fh:
    json.dump(ops, fh, indent=2)
PY

trap 'rm -f "$batch_path"' EXIT

openclaw config set --batch-file "$batch_path"
openclaw config validate

echo
echo "OpenClaw bootstrap complete."
echo "Workspace: $workspace_dir"
echo "Plugin dir: $plugin_dir"
echo "Prompt file: $prompt_file"
echo "Gateway token: $gateway_token"
