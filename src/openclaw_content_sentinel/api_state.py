from __future__ import annotations

from pathlib import Path
from typing import Any, cast

from .config import AppConfig
from .identity import build_publish_identity, file_sha256, sha256_json
from .storage import RunStore
from .utils import dump_json, ensure_dir, load_json, now_utc, read_text, write_text


def _json_items(payload: Any) -> list[dict[str, Any]]:
    if not isinstance(payload, dict):
        return []
    items = payload.get("items")
    if not isinstance(items, list):
        return []
    return [dict(item) for item in items if isinstance(item, dict)]


class ApiStateStore:
    def __init__(self, config: AppConfig) -> None:
        self.config = config
        self.root = ensure_dir(config.data_dir / "api")
        self._tables = {
            "projects": self.root / "projects.json",
            "brand_profiles": self.root / "brand_profiles.json",
            "campaigns": self.root / "campaigns.json",
            "templates": self.root / "templates.json",
            "candidates": self.root / "candidates.json",
            "approvals": self.root / "approvals.json",
            "render_jobs": self.root / "render_jobs.json",
            "publish_jobs": self.root / "publish_jobs.json",
            "analytics_snapshots": self.root / "analytics_snapshots.json",
            "replays": self.root / "replays.json",
            "request_log": self.root / "request_log.json",
            "event_log": self.root / "event_log.json",
            "schedules": self.root / "schedules.json",
        }

    def bootstrap(self) -> None:
        ensure_dir(self.root)
        for path in self._tables.values():
            if not path.exists():
                dump_json(path, {"items": []})
        self._seed_default_templates()
        self._seed_default_schedule_state()

    def _load_items(self, name: str) -> list[dict[str, Any]]:
        payload = load_json(self._tables[name], default={"items": []}) or {"items": []}
        return _json_items(payload)

    def _save_items(self, name: str, items: list[dict[str, Any]]) -> Path:
        path = self._tables[name]
        dump_json(path, {"items": items, "updated_at": now_utc()})
        return path

    def _seed_default_templates(self) -> None:
        items = self._load_items("templates")
        if items:
            return
        defaults = [
            {
                "template_id": "editorial_split_social_card",
                "name": "Editorial Split Social Card",
                "asset_type": "social_card",
                "layout": "editorial_split",
                "dimensions": {"width": 1200, "height": 630},
                "platforms": ["linkedin", "facebook", "x"],
                "style_tokens": ["crystalline", "editorial", "b2b-tech"],
                "render_backend": "procedural_or_comfyui",
                "created_at": now_utc(),
                "updated_at": now_utc(),
                "schema_version": self.config.schema_version,
            },
            {
                "template_id": "stacked_brief_social_card",
                "name": "Stacked Brief Social Card",
                "asset_type": "social_card",
                "layout": "stacked_brief",
                "dimensions": {"width": 1200, "height": 630},
                "platforms": ["linkedin", "facebook", "x"],
                "style_tokens": ["crystalline", "stacked", "brief"],
                "render_backend": "procedural_or_comfyui",
                "created_at": now_utc(),
                "updated_at": now_utc(),
                "schema_version": self.config.schema_version,
            },
            {
                "template_id": "signal_focus_social_card",
                "name": "Signal Focus Social Card",
                "asset_type": "social_card",
                "layout": "signal_focus",
                "dimensions": {"width": 1200, "height": 630},
                "platforms": ["linkedin", "facebook", "x"],
                "style_tokens": ["crystalline", "signal", "operator"],
                "render_backend": "procedural_or_comfyui",
                "created_at": now_utc(),
                "updated_at": now_utc(),
                "schema_version": self.config.schema_version,
            },
            {
                "template_id": "carousel_standard_1080x1350",
                "name": "Carousel Standard 1080x1350",
                "asset_type": "carousel",
                "layout": "carousel_standard",
                "dimensions": {"width": 1080, "height": 1350},
                "platforms": ["linkedin", "facebook"],
                "style_tokens": ["carousel", "crystalline", "slides"],
                "default_slide_count": 4,
                "render_backend": "procedural",
                "created_at": now_utc(),
                "updated_at": now_utc(),
                "schema_version": self.config.schema_version,
            },
            {
                "template_id": "cover_card_1600x900",
                "name": "Cover Card 1600x900",
                "asset_type": "cover_card",
                "layout": "cover_card",
                "dimensions": {"width": 1600, "height": 900},
                "platforms": ["linkedin", "facebook", "x"],
                "style_tokens": ["cover", "crystalline", "hero"],
                "render_backend": "procedural_or_comfyui",
                "created_at": now_utc(),
                "updated_at": now_utc(),
                "schema_version": self.config.schema_version,
            },
        ]
        self._save_items("templates", cast(list[dict[str, Any]], defaults))

    def _seed_default_schedule_state(self) -> None:
        items = self._load_items("schedules")
        if items:
            return
        store = RunStore(self.config)
        control = store.load_control()
        payload = {
            "schedule_id": "default",
            "desired_state": {
                "automation_paused": bool(control.get("automation_paused", False)),
                "auto_publish_enabled": bool(self.config.auto_publish_enabled),
                "approval_required": bool(self.config.approval_required),
                "daily_cron_enabled": True,
                "heartbeat_enabled": True,
                "operator_timezone": self.config.operator_timezone,
                "active_brief_id": "",
            },
            "observed_state": {
                "automation_paused": bool(control.get("automation_paused", False)),
                "auto_publish_enabled": bool(self.config.auto_publish_enabled),
                "approval_required": bool(self.config.approval_required),
                "daily_cron_enabled": True,
                "heartbeat_enabled": True,
                "operator_timezone": self.config.operator_timezone,
                "active_brief_id": "",
            },
            "updated_at": now_utc(),
            "updated_by": "system",
            "schema_version": self.config.schema_version,
        }
        self._save_items("schedules", [payload])

    def list_projects(self) -> list[dict[str, Any]]:
        return self._load_items("projects")

    def create_project(self, name: str, description: str = "") -> dict[str, Any]:
        items = self._load_items("projects")
        normalized_name = str(name or "").strip() or "default"
        project_key = sha256_json({"name": normalized_name.lower()})
        existing = next((item for item in items if item.get("project_key") == project_key), None)
        if existing:
            return existing
        payload = {
            "project_id": f"proj_{project_key[:12]}",
            "project_key": project_key,
            "name": normalized_name,
            "description": str(description or "").strip(),
            "status": "active",
            "created_at": now_utc(),
            "updated_at": now_utc(),
            "schema_version": self.config.schema_version,
        }
        items.append(payload)
        self._save_items("projects", items)
        return payload

    def list_brand_profiles(self) -> list[dict[str, Any]]:
        items = self._load_items("brand_profiles")
        default_content = (
            read_text(self.config.brand_profile_file)
            if self.config.brand_profile_file.exists()
            else ""
        )
        if not any(item.get("profile_id") == "default" for item in items):
            items.insert(
                0,
                {
                    "profile_id": "default",
                    "name": "Default brand profile",
                    "content": default_content,
                    "active": True,
                    "source_file": str(self.config.brand_profile_file),
                    "created_at": now_utc(),
                    "updated_at": now_utc(),
                    "schema_version": self.config.schema_version,
                },
            )
        return items

    def upsert_brand_profile(
        self,
        profile_id: str,
        name: str,
        content: str,
        active: bool = True,
    ) -> dict[str, Any]:
        items = self._load_items("brand_profiles")
        profile_id = str(profile_id or "default").strip() or "default"
        record = {
            "profile_id": profile_id,
            "name": str(name or "Brand profile").strip() or "Brand profile",
            "content": str(content or "").strip(),
            "active": bool(active),
            "source_file": str(
                self.config.brand_profile_file
                if profile_id == "default"
                else self.root / f"brand-profile-{profile_id}.md"
            ),
            "updated_at": now_utc(),
            "schema_version": self.config.schema_version,
        }
        existing = next((item for item in items if item.get("profile_id") == profile_id), None)
        if existing:
            record["created_at"] = existing.get("created_at") or now_utc()
            items = [record if item.get("profile_id") == profile_id else item for item in items]
        else:
            record["created_at"] = now_utc()
            items.append(record)
        self._save_items("brand_profiles", items)
        target_path = (
            self.config.brand_profile_file
            if profile_id == "default"
            else Path(cast(str, record["source_file"]))
        )
        write_text(target_path, cast(str, record["content"]))
        return record

    def list_campaigns(self) -> list[dict[str, Any]]:
        return self._load_items("campaigns")

    def create_campaign(
        self,
        *,
        name: str,
        objective: str = "",
        frequency: str = "daily",
        publish_windows: list[str] | None = None,
        approval_policy: str = "",
        target_platforms: list[str] | None = None,
        project_id: str = "proj_default",
        active: bool = True,
    ) -> dict[str, Any]:
        items = self._load_items("campaigns")
        normalized_name = str(name or "").strip() or "Daily campaign"
        campaign_key = sha256_json({"project_id": project_id, "name": normalized_name.lower()})
        existing = next((item for item in items if item.get("campaign_key") == campaign_key), None)
        if existing:
            return existing
        payload = {
            "campaign_id": f"camp_{campaign_key[:12]}",
            "campaign_key": campaign_key,
            "project_id": str(project_id or "proj_default").strip() or "proj_default",
            "name": normalized_name,
            "objective": str(objective or "").strip(),
            "frequency": str(frequency or "daily").strip() or "daily",
            "publish_windows": [
                str(item).strip() for item in (publish_windows or []) if str(item).strip()
            ],
            "approval_policy": str(approval_policy or self.config.approval_policy).strip()
            or self.config.approval_policy,
            "target_platforms": [
                str(item).strip()
                for item in (target_platforms or self.config.default_targets)
                if str(item).strip()
            ],
            "active": bool(active),
            "created_at": now_utc(),
            "updated_at": now_utc(),
            "schema_version": self.config.schema_version,
        }
        items.append(payload)
        self._save_items("campaigns", items)
        return payload

    def update_campaign(self, campaign_id: str, patch: dict[str, Any]) -> dict[str, Any]:
        items = self._load_items("campaigns")
        updated = None
        for item in items:
            if item.get("campaign_id") != campaign_id:
                continue
            item.update(
                {
                    "name": str(patch.get("name") or item.get("name") or "").strip()
                    or item.get("name")
                    or "Campaign",
                    "objective": str(patch.get("objective") or item.get("objective") or "").strip(),
                    "frequency": str(
                        patch.get("frequency") or item.get("frequency") or "daily"
                    ).strip()
                    or "daily",
                    "publish_windows": [
                        str(value).strip()
                        for value in (
                            patch.get("publish_windows") or item.get("publish_windows") or []
                        )
                        if str(value).strip()
                    ],
                    "approval_policy": str(
                        patch.get("approval_policy")
                        or item.get("approval_policy")
                        or self.config.approval_policy
                    ).strip()
                    or self.config.approval_policy,
                    "target_platforms": [
                        str(value).strip()
                        for value in (
                            patch.get("target_platforms")
                            or item.get("target_platforms")
                            or self.config.default_targets
                        )
                        if str(value).strip()
                    ],
                    "active": bool(
                        item.get("active") if patch.get("active") is None else patch.get("active")
                    ),
                    "updated_at": now_utc(),
                }
            )
            updated = dict(item)
            break
        if updated is None:
            raise FileNotFoundError(f"Unknown campaign: {campaign_id}")
        self._save_items("campaigns", items)
        return updated

    def list_templates(self, asset_type: str = "") -> list[dict[str, Any]]:
        items = self._load_items("templates")
        if asset_type:
            items = [
                item for item in items if str(item.get("asset_type") or "").strip() == asset_type
            ]
        items.sort(
            key=lambda item: (str(item.get("asset_type") or ""), str(item.get("template_id") or ""))
        )
        return items

    def upsert_template(
        self,
        *,
        template_id: str,
        name: str,
        asset_type: str,
        layout: str,
        dimensions: dict[str, Any],
        platforms: list[str],
        style_tokens: list[str],
        render_backend: str = "procedural",
        default_slide_count: int | None = None,
    ) -> dict[str, Any]:
        items = self._load_items("templates")
        record = {
            "template_id": str(template_id or "").strip(),
            "name": str(name or "Template").strip() or "Template",
            "asset_type": str(asset_type or "social_card").strip() or "social_card",
            "layout": str(layout or "editorial_split").strip() or "editorial_split",
            "dimensions": {
                "width": int((dimensions or {}).get("width") or 1200),
                "height": int((dimensions or {}).get("height") or 630),
            },
            "platforms": [str(item).strip() for item in platforms if str(item).strip()],
            "style_tokens": [str(item).strip() for item in style_tokens if str(item).strip()],
            "render_backend": str(render_backend or "procedural").strip() or "procedural",
            "default_slide_count": int(default_slide_count or 0) if default_slide_count else None,
            "updated_at": now_utc(),
            "schema_version": self.config.schema_version,
        }
        existing = next(
            (item for item in items if item.get("template_id") == record["template_id"]), None
        )
        if existing:
            record["created_at"] = existing.get("created_at") or now_utc()
            items = [
                record if item.get("template_id") == record["template_id"] else item
                for item in items
            ]
        else:
            record["created_at"] = now_utc()
            items.append(record)
        self._save_items("templates", items)
        return record

    def list_candidates(self, run_id: str = "") -> list[dict[str, Any]]:
        items = self._load_items("candidates")
        if run_id:
            return [item for item in items if item.get("run_id") == run_id]
        return items

    def create_candidate(self, run: dict[str, Any], variant_id: str = "master") -> dict[str, Any]:
        store = RunStore(self.config)
        run_id = str(run.get("run_id") or "")
        draft_hashes: dict[str, str] = {}
        for platform in ("article", "linkedin", "facebook", "x", "image_prompt"):
            path = store.run_dir(run_id) / "drafts" / f"{platform}.md"
            if path.exists():
                draft_hashes[platform] = file_sha256(path)
        image_path = store.run_dir(run_id) / str(
            (run.get("outputs") or {}).get("image_asset") or "media/social_card.png"
        )
        candidate_key = sha256_json(
            {
                "run_id": run_id,
                "variant_id": variant_id,
                "draft_hashes": draft_hashes,
                "image_asset_hash": file_sha256(image_path),
                "input_hash": ((run.get("identity") or {}).get("input_hash") or ""),
            }
        )
        items = self._load_items("candidates")
        existing = next(
            (item for item in items if item.get("candidate_key") == candidate_key), None
        )
        if existing:
            return existing
        payload = {
            "candidate_id": f"cand_{candidate_key[:12]}",
            "candidate_key": candidate_key,
            "run_id": run_id,
            "variant_id": variant_id,
            "draft_hashes": draft_hashes,
            "image_asset_hash": file_sha256(image_path),
            "approval_state": str(run.get("approval_state") or "pending"),
            "quality_score": (run.get("quality_gate") or {}).get("score"),
            "created_at": now_utc(),
            "updated_at": now_utc(),
            "schema_version": self.config.schema_version,
        }
        items.append(payload)
        self._save_items("candidates", items)
        return payload

    def list_approvals(self, run_id: str = "") -> list[dict[str, Any]]:
        items = self._load_items("approvals")
        if run_id:
            return [item for item in items if item.get("run_id") == run_id]
        return items

    def latest_approval_for_run(self, run_id: str) -> dict[str, Any] | None:
        items = self.list_approvals(run_id=run_id)
        if not items:
            return None
        items.sort(
            key=lambda item: (
                str(item.get("updated_at") or ""),
                str(item.get("approval_id") or ""),
            ),
            reverse=True,
        )
        return items[0]

    def create_approval_record(
        self,
        run_id: str,
        candidate_ref: str,
        decision: str,
        note: str = "",
        actor: str = "api",
        request_id: str = "",
    ) -> dict[str, Any]:
        items = self._load_items("approvals")
        approval_key = sha256_json(
            {
                "run_id": run_id,
                "candidate_ref": candidate_ref,
                "decision": str(decision or "").strip().lower(),
                "note": str(note or "").strip(),
            }
        )
        existing = next((item for item in items if item.get("approval_key") == approval_key), None)
        if existing:
            return existing
        payload = {
            "approval_id": f"appr_{approval_key[:12]}",
            "approval_key": approval_key,
            "run_id": run_id,
            "candidate_ref": candidate_ref,
            "decision": str(decision or "").strip().lower(),
            "note": str(note or "").strip(),
            "actor": str(actor or "api").strip() or "api",
            "request_id": str(request_id or "").strip(),
            "created_at": now_utc(),
            "updated_at": now_utc(),
            "schema_version": self.config.schema_version,
        }
        items.append(payload)
        self._save_items("approvals", items)
        return payload

    def list_render_jobs(self, run_id: str = "", limit: int = 100) -> list[dict[str, Any]]:
        items = self._load_items("render_jobs")
        if run_id:
            items = [item for item in items if item.get("run_id") == run_id]
        items.sort(
            key=lambda item: (
                str(item.get("updated_at") or ""),
                str(item.get("render_job_id") or ""),
            ),
            reverse=True,
        )
        return items[: max(1, min(limit, 500))]

    def create_render_job(
        self,
        *,
        run: dict[str, Any],
        asset_type: str,
        template_id: str,
        slide_count: int = 4,
        actor: str = "api",
        request_id: str = "",
        idempotency_seed: str = "",
    ) -> dict[str, Any]:
        items = self._load_items("render_jobs")
        run_id = str(run.get("run_id") or "")
        normalized_asset_type = str(asset_type or "social_card").strip().lower() or "social_card"
        normalized_template_id = str(template_id or "").strip() or {
            "social_card": "editorial_split_social_card",
            "carousel": "carousel_standard_1080x1350",
            "cover_card": "cover_card_1600x900",
        }.get(normalized_asset_type, "editorial_split_social_card")
        idempotency_key = (
            sha256_json(
                {
                    "seed": str(idempotency_seed or "").strip(),
                    "run_id": run_id,
                    "asset_type": normalized_asset_type,
                    "template_id": normalized_template_id,
                }
            )
            if str(idempotency_seed or "").strip()
            else sha256_json(
                {
                    "run_id": run_id,
                    "asset_type": normalized_asset_type,
                    "template_id": normalized_template_id,
                    "slide_count": int(slide_count or 4),
                }
            )
        )
        existing = next(
            (
                item
                for item in items
                if item.get("run_id") == run_id
                and item.get("asset_type") == normalized_asset_type
                and item.get("template_id") == normalized_template_id
                and item.get("idempotency_key") == idempotency_key
            ),
            None,
        )
        if existing:
            return existing
        render_key = sha256_json(
            {
                "run_id": run_id,
                "asset_type": normalized_asset_type,
                "template_id": normalized_template_id,
                "idempotency_key": idempotency_key,
            }
        )
        payload = {
            "render_job_id": f"render_{render_key[:12]}",
            "run_id": run_id,
            "asset_type": normalized_asset_type,
            "template_id": normalized_template_id,
            "slide_count": max(1, min(int(slide_count or 4), 8)),
            "state": "QUEUED",
            "idempotency_key": idempotency_key,
            "request_id": str(request_id or "").strip(),
            "actor": str(actor or "api").strip() or "api",
            "output_assets": [],
            "primary_asset": "",
            "manifest_path": "",
            "last_error": "",
            "created_at": now_utc(),
            "updated_at": now_utc(),
            "schema_version": self.config.schema_version,
        }
        items.append(payload)
        self._save_items("render_jobs", items)
        return payload

    def update_render_job(
        self,
        render_job_id: str,
        *,
        state: str,
        output_assets: list[str] | None = None,
        primary_asset: str = "",
        manifest_path: str = "",
        last_error: str = "",
    ) -> dict[str, Any]:
        items = self._load_items("render_jobs")
        updated = None
        for item in items:
            if item.get("render_job_id") != render_job_id:
                continue
            item["state"] = str(state or item.get("state") or "QUEUED")
            item["updated_at"] = now_utc()
            if output_assets is not None:
                item["output_assets"] = [
                    str(asset) for asset in output_assets if str(asset).strip()
                ]
            if primary_asset:
                item["primary_asset"] = str(primary_asset)
            if manifest_path:
                item["manifest_path"] = str(manifest_path)
            if last_error:
                item["last_error"] = str(last_error)
            updated = dict(item)
            break
        if updated is None:
            raise FileNotFoundError(f"Unknown render job: {render_job_id}")
        self._save_items("render_jobs", items)
        return updated

    def list_publish_jobs(self, run_id: str = "", limit: int = 100) -> list[dict[str, Any]]:
        items = self._load_items("publish_jobs")
        if run_id:
            items = [item for item in items if item.get("run_id") == run_id]
        items.sort(
            key=lambda item: (str(item.get("updated_at") or ""), str(item.get("job_id") or "")),
            reverse=True,
        )
        return items[: max(1, min(limit, 500))]

    def create_publish_job_records(
        self,
        run: dict[str, Any],
        platforms: list[str],
        candidate_ref: str = "",
        approval_ref: str = "",
        actor: str = "api",
        request_id: str = "",
        idempotency_seed: str = "",
    ) -> list[dict[str, Any]]:
        items = self._load_items("publish_jobs")
        store = RunStore(self.config)
        run_id = str(run.get("run_id") or "")
        created: list[dict[str, Any]] = []
        for platform in platforms:
            draft_path = store.run_dir(run_id) / "drafts" / f"{platform}.md"
            draft_text = read_text(draft_path) if draft_path.exists() else ""
            publish_identity = build_publish_identity(run, platform, draft_text)
            idempotency_key = (
                sha256_json({"seed": str(idempotency_seed).strip(), "platform": platform})
                if str(idempotency_seed or "").strip()
                else publish_identity["idempotency_key"]
            )
            existing = next(
                (
                    item
                    for item in items
                    if item.get("run_id") == run_id
                    and item.get("platform") == platform
                    and item.get("idempotency_key") == idempotency_key
                ),
                None,
            )
            if existing:
                created.append(existing)
                continue
            job_key = sha256_json(
                {"run_id": run_id, "platform": platform, "idempotency_key": idempotency_key}
            )
            payload = {
                "job_id": f"pub_{job_key[:12]}",
                "run_id": run_id,
                "candidate_ref": candidate_ref,
                "approval_ref": approval_ref,
                "platform": platform,
                "adapter_version": str(run.get("publish_adapter_version") or ""),
                "idempotency_key": idempotency_key,
                "publish_target_hash": publish_identity["publish_target_hash"],
                "canonical_post_hash": publish_identity["canonical_post_hash"],
                "state": "QUEUED",
                "attempt_count": 0,
                "final_url": "",
                "proof_id": "",
                "request_id": str(request_id or "").strip(),
                "actor": str(actor or "api").strip() or "api",
                "last_error": "",
                "created_at": now_utc(),
                "updated_at": now_utc(),
                "schema_version": self.config.schema_version,
            }
            items.append(payload)
            created.append(payload)
        self._save_items("publish_jobs", items)
        return created

    def update_publish_job(
        self,
        job_id: str,
        *,
        state: str,
        final_url: str = "",
        proof_id: str = "",
        attempt_count: int | None = None,
        last_error: str = "",
    ) -> dict[str, Any]:
        items = self._load_items("publish_jobs")
        updated = None
        for item in items:
            if item.get("job_id") != job_id:
                continue
            item["state"] = state
            item["updated_at"] = now_utc()
            if final_url:
                item["final_url"] = final_url
            if proof_id:
                item["proof_id"] = proof_id
            if attempt_count is not None:
                item["attempt_count"] = int(attempt_count)
            if last_error:
                item["last_error"] = last_error
            updated = dict(item)
            break
        if updated is None:
            raise FileNotFoundError(f"Unknown publish job: {job_id}")
        self._save_items("publish_jobs", items)
        return updated

    def list_analytics_snapshots(self, run_id: str = "", limit: int = 100) -> list[dict[str, Any]]:
        items = self._load_items("analytics_snapshots")
        if run_id:
            items = [item for item in items if item.get("run_id") == run_id]
        items.sort(
            key=lambda item: (
                str(item.get("captured_at") or ""),
                str(item.get("snapshot_id") or ""),
            ),
            reverse=True,
        )
        return items[: max(1, min(limit, 500))]

    def create_analytics_snapshot(
        self,
        *,
        scope: str,
        payload: dict[str, Any],
        run_id: str = "",
        actor: str = "system",
        request_id: str = "",
    ) -> dict[str, Any]:
        items = self._load_items("analytics_snapshots")
        snapshot_key = sha256_json(
            {
                "scope": str(scope or "system").strip(),
                "run_id": str(run_id or "").strip(),
                "payload": payload,
            }
        )
        record = {
            "snapshot_id": f"snap_{snapshot_key[:12]}",
            "snapshot_key": snapshot_key,
            "scope": str(scope or "system").strip() or "system",
            "run_id": str(run_id or "").strip(),
            "payload": payload,
            "actor": str(actor or "system").strip() or "system",
            "request_id": str(request_id or "").strip(),
            "captured_at": now_utc(),
            "schema_version": self.config.schema_version,
        }
        items.append(record)
        self._save_items("analytics_snapshots", items[-500:])
        return record

    def list_replays(self, run_id: str = "") -> list[dict[str, Any]]:
        items = self._load_items("replays")
        if run_id:
            return [item for item in items if item.get("run_id") == run_id]
        return items

    def create_replay_record(
        self,
        run_id: str,
        stages: list[str],
        replay_window: str,
        actor: str = "api",
        request_id: str = "",
    ) -> dict[str, Any]:
        items = self._load_items("replays")
        replay_key = sha256_json(
            {
                "run_id": run_id,
                "stages": stages,
                "replay_window": replay_window,
            }
        )
        payload = {
            "replay_id": f"replay_{replay_key[:12]}",
            "replay_key": replay_key,
            "run_id": run_id,
            "stages": list(stages),
            "replay_window": replay_window,
            "actor": str(actor or "api").strip() or "api",
            "request_id": str(request_id or "").strip(),
            "status": "completed",
            "created_at": now_utc(),
            "updated_at": now_utc(),
            "schema_version": self.config.schema_version,
        }
        items.append(payload)
        self._save_items("replays", items)
        return payload

    def list_events(self, event_type: str = "", limit: int = 200) -> list[dict[str, Any]]:
        items = self._load_items("event_log")
        if event_type:
            items = [item for item in items if item.get("event_type") == event_type]
        items.sort(
            key=lambda item: (str(item.get("created_at") or ""), str(item.get("event_id") or "")),
            reverse=True,
        )
        return items[: max(1, min(limit, 1000))]

    def log_event(
        self,
        *,
        event_type: str,
        payload: dict[str, Any],
        actor: str = "system",
        request_id: str = "",
        run_id: str = "",
    ) -> dict[str, Any]:
        items = self._load_items("event_log")
        event_key = sha256_json(
            {
                "event_type": str(event_type or "").strip(),
                "run_id": str(run_id or "").strip(),
                "payload": payload,
                "request_id": str(request_id or "").strip(),
            }
        )
        record = {
            "event_id": f"evt_{event_key[:12]}",
            "event_key": event_key,
            "event_type": str(event_type or "event").strip() or "event",
            "run_id": str(run_id or "").strip(),
            "payload": payload,
            "actor": str(actor or "system").strip() or "system",
            "request_id": str(request_id or "").strip(),
            "created_at": now_utc(),
            "schema_version": self.config.schema_version,
        }
        items.append(record)
        self._save_items("event_log", items[-1000:])
        return record

    def get_schedule_state(self) -> dict[str, Any]:
        items = self._load_items("schedules")
        if items:
            return items[0]
        self._seed_default_schedule_state()
        return self._load_items("schedules")[0]

    def update_schedule_state(
        self,
        *,
        desired_patch: dict[str, Any] | None = None,
        observed_patch: dict[str, Any] | None = None,
        actor: str = "system",
        request_id: str = "",
    ) -> dict[str, Any]:
        record = self.get_schedule_state()
        desired_state = dict(record.get("desired_state") or {})
        observed_state = dict(record.get("observed_state") or {})
        desired_state.update(
            {key: value for key, value in (desired_patch or {}).items() if value is not None}
        )
        observed_state.update(
            {key: value for key, value in (observed_patch or {}).items() if value is not None}
        )
        updated = {
            **record,
            "desired_state": desired_state,
            "observed_state": observed_state,
            "updated_at": now_utc(),
            "updated_by": str(actor or "system").strip() or "system",
            "request_id": str(request_id or "").strip(),
            "schema_version": self.config.schema_version,
        }
        self._save_items("schedules", [updated])
        return updated

    def log_request(
        self,
        *,
        request_id: str,
        actor: str,
        method: str,
        path: str,
        status_code: int,
        latency_ms: int,
        idempotency_key: str = "",
        target_resource: str = "",
        result: str = "",
    ) -> dict[str, Any]:
        items = self._load_items("request_log")
        payload = {
            "request_id": request_id,
            "actor": actor,
            "method": method,
            "path": path,
            "status_code": int(status_code),
            "latency_ms": int(latency_ms),
            "idempotency_key": str(idempotency_key or "").strip(),
            "target_resource": str(target_resource or "").strip(),
            "result": str(result or "").strip(),
            "created_at": now_utc(),
        }
        items.append(payload)
        items = items[-1000:]
        self._save_items("request_log", items)
        return payload
