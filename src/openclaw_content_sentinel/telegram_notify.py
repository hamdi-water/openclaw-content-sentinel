from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from urllib import error, request

from .config import AppConfig
from .storage import RunStore
from .utils import read_text

TELEGRAM_API_BASE = "https://api.telegram.org"
TELEGRAM_MAX_TEXT = 3900


def _chat_id_from_target(target: str | None) -> str:
    value = (target or "").strip()
    if value.startswith("telegram:"):
        return value.split(":", 1)[1].strip()
    return value


def _trim_text(text: str, limit: int = TELEGRAM_MAX_TEXT) -> str:
    clean = (text or "").strip()
    if len(clean) <= limit:
        return clean
    return clean[: limit - 4].rstrip() + " ..."


def send_telegram_message(
    bot_token: str,
    chat_id: str,
    text: str,
    reply_markup: dict[str, Any] | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "chat_id": chat_id,
        "text": _trim_text(text),
        "disable_web_page_preview": True,
    }
    if reply_markup:
        payload["reply_markup"] = reply_markup

    data = json.dumps(payload, ensure_ascii=True).encode("utf-8")
    # noqa: S310
    req = request.Request(  # noqa: S310
        f"{TELEGRAM_API_BASE}/bot{bot_token}/sendMessage",
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with request.urlopen(req, timeout=20) as response:  # noqa: S310
            body = response.read().decode("utf-8", errors="replace")
    except error.HTTPError as exc:  # pragma: no cover - network/runtime dependent
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"Telegram sendMessage failed: HTTP {exc.code} {detail}") from exc
    except error.URLError as exc:  # pragma: no cover - network/runtime dependent
        raise RuntimeError(f"Telegram sendMessage failed: {exc.reason}") from exc

    payload = json.loads(body or "{}")
    if not payload.get("ok"):
        raise RuntimeError(f"Telegram sendMessage returned error: {payload}")
    return payload


def build_preview_message(config: AppConfig, run_id: str) -> str:
    store = RunStore(config)
    run = store.load_run(run_id)
    preview_rel = (run.get("outputs") or {}).get("telegram", "ops/telegram_preview.md")
    preview_text = read_text(store.run_dir(run_id) / Path(preview_rel)).strip()
    return preview_text or f"Run {run_id}\nStatus: {run.get('status', 'unknown')}"


def build_preview_reply_markup(run_id: str) -> dict[str, Any]:
    command_rows = [
        [f"/status {run_id}", f"/logs {run_id}"],
        [f"/approve {run_id}", f"/reject {run_id}"],
        [f"/publish {run_id}", f"/retry {run_id} linkedin"],
        ["/run-now", "/doctor"],
    ]
    return {
        "keyboard": [[{"text": item} for item in row] for row in command_rows],
        "resize_keyboard": True,
        "is_persistent": True,
        "input_field_placeholder": f"Run {run_id}: status, approve, reject, publish, or logs",
    }


def send_run_preview(config: AppConfig, run_id: str) -> dict[str, Any]:
    bot_token = (config.telegram_bot_token or "").strip()
    chat_id = _chat_id_from_target(config.telegram_target)
    if not bot_token:
        raise RuntimeError(
            "OPENCLAW_TELEGRAM_BOT_TOKEN is not configured in the workspace environment."
        )
    if not chat_id:
        raise RuntimeError("OPENCLAW_SENTINEL_TELEGRAM_TARGET is not configured.")

    message = build_preview_message(config, run_id)
    response = send_telegram_message(
        bot_token,
        chat_id,
        message,
        reply_markup=build_preview_reply_markup(run_id),
    )
    return {
        "chat_id": chat_id,
        "message_id": ((response.get("result") or {}).get("message_id")),
        "response": response,
    }
