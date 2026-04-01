from __future__ import annotations

import hashlib
import hmac
import json
import urllib.request
from typing import Any
from urllib.parse import urlparse

from .config import AppConfig
from .utils import dump_json, load_json, now_utc


class WebhookRegistry:
    """Ref §33.1: Manage webhook endpoints and secrets."""

    def __init__(self, config: AppConfig) -> None:
        self.config = config
        self.registry_path = config.data_dir / "webhooks.json"

    def list_webhooks(self) -> list[dict[str, Any]]:
        return load_json(self.registry_path, default=[])

    def register_webhook(
        self, url: str, secret: str, events: list[str] | None = None
    ) -> dict[str, Any]:
        webhooks = self.list_webhooks()
        parsed = urlparse(url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValueError("Webhook URL must use http or https.")
        entry = {
            "webhook_id": hashlib.sha256(url.encode()).hexdigest()[:8],
            "url": url,
            "secret": secret,
            "events": events or ["*"],
            "created_at": now_utc(),
        }
        webhooks.append(entry)
        dump_json(self.registry_path, webhooks)
        return entry

    def remove_webhook(self, webhook_id: str) -> bool:
        webhooks = self.list_webhooks()
        new_webhooks = [w for w in webhooks if w["webhook_id"] != webhook_id]
        if len(new_webhooks) < len(webhooks):
            dump_json(self.registry_path, new_webhooks)
            return True
        return False


class WebhookDispatcher:
    """Ref §33.3: Dispatch signed payloads to registered webhooks."""

    def __init__(self, config: AppConfig) -> None:
        self.config = config
        self.registry = WebhookRegistry(config)

    def sign_payload(self, payload: str, secret: str) -> str:
        """Sign payload using HMAC-SHA256."""
        return hmac.new(
            secret.encode("utf-8"),
            payload.encode("utf-8"),
            hashlib.sha256
        ).hexdigest()

    async def dispatch(self, event_type: str, payload: dict[str, Any]) -> list[dict[str, Any]]:
        """Send event to all matching webhooks."""
        webhooks = self.registry.list_webhooks()
        results = []

        json_payload = json.dumps({
            "event": event_type,
            "timestamp": now_utc(),
            "payload": payload
        })

        for hook in webhooks:
            if "*" not in hook["events"] and event_type not in hook["events"]:
                continue

            signature = self.sign_payload(json_payload, hook["secret"])
            headers = {
                "Content-Type": "application/json",
                "X-OCS-Signature": signature,
                "X-OCS-Event": event_type,
            }

            # Use urllib for zero-dependency dispatch
            try:
                req = urllib.request.Request(  # noqa: S310
                    hook["url"],
                    data=json_payload.encode("utf-8"),
                    headers=headers,
                    method="POST"
                )
                with urllib.request.urlopen(req, timeout=10) as response:  # noqa: S310
                    results.append({
                        "webhook_id": hook["webhook_id"],
                        "status": response.status,
                        "success": True
                    })
            except Exception as e:
                results.append({
                    "webhook_id": hook["webhook_id"],
                    "error": str(e),
                    "success": False
                })

        return results
