from __future__ import annotations

import secrets

from .models import PostCandidate
from .prompts import PromptManager


class VariationGenerator:
    """Ref §31.1: Generate content variations for social media replayability."""

    def __init__(self, prompt_manager: PromptManager) -> None:
        self.prompt_manager = prompt_manager

    def generate_variation(
        self, original_candidate: PostCandidate, seed_text: str = ""
    ) -> PostCandidate:
        """Create a non-identical variation of a post for evergreen rotation."""
        content = original_candidate.content

        # Simple heuristic variations for MVP
        variation_prefixes = [
            "ICYMI: ",
            "Throwback to this insight: ",
            "Still relevant: ",
            "Flashback on: ",
            "Did you miss this? ",
        ]

        prefix = secrets.choice(variation_prefixes)
        new_content = f"{prefix}{content}"

        # In a real production system, we would call an LLM here with a 'variation' prompt.
        # We simulate the variation by appending a unique seed if provided.
        if seed_text:
            new_content += f"\n\n[#Ref: {seed_text}]"

        return PostCandidate(
            candidate_id=(
                f"{original_candidate.candidate_id}_var_{100 + secrets.randbelow(900)}"
            ),
            run_id=original_candidate.run_id,
            platform=original_candidate.platform,
            content=new_content,
            image_url=original_candidate.image_url,
            metadata={
                **original_candidate.metadata,
                "is_variation": True,
                "parent_id": original_candidate.candidate_id,
            },
        )
