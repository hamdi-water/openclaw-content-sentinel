from __future__ import annotations

from pathlib import Path
from typing import Any

from jinja2 import Environment, FileSystemLoader, select_autoescape

from .config import AppConfig


class PromptManager:
    """Ref §24.1: Centralized prompt management with versioned bundles."""

    def __init__(self, config: AppConfig) -> None:
        self.config = config
        # Default to project-relative config/prompts
        self.base_dir = Path("config/prompts")
        if not self.base_dir.exists():
            # Fallback to absolute path if needed
            self.base_dir = Path(config.data_dir) / "prompts"

        self.env = Environment(
            loader=FileSystemLoader(str(self.base_dir)), autoescape=select_autoescape()
        )

    def render(self, bundle_id: str, template_name: str, context: dict[str, Any]) -> str:
        """Render a template from a specific bundle."""
        lang = context.get("daily_input", {}).get("language") or "en"
        base_name = template_name.replace(".md", "")
        variant_error: Exception | None = None

        # Try language-specific variant first
        if lang == "fr":
            variant = f"{bundle_id}/{base_name}_fr.md"
            try:
                template = self.env.get_template(variant)
                return template.render(**context)
            except Exception as exc:
                variant_error = exc

        template_path = f"{bundle_id}/{template_name}"
        try:
            template = self.env.get_template(template_path)
            return template.render(**context)
        except Exception as e:
            # Fallback for simple testing or missing files
            if ".md" not in template_path:
                return self.render(bundle_id, f"{template_name}.md", context)
            if variant_error is not None:
                raise e from variant_error
            raise e

    def route_model(self, task: str) -> str:
        """Ref §32.4: Route to appropriate model based on task complexity."""
        complexity_map = {
            "drafting": "high",
            "research": "medium",
            "cleanup": "low",
            "critic": "high",
        }
        complexity = complexity_map.get(task, "medium")

        if complexity == "high":
            return "llama-3.3-70b"
        elif complexity == "medium":
            return "llama-3.1-8b"
        else:
            return "gpt-4o-mini"  # Example mapping
