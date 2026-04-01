# ruff: noqa: E501
from __future__ import annotations

import re
from pathlib import Path
from typing import Any, cast

from .editorial import summarize_brand_profile
from .memory import humanize_memory_hit
from .utils import read_text


def _clean_sentence(text: str, fallback: str = "") -> str:
    payload = " ".join((text or "").split())
    return payload or fallback


def _trim_words(text: str, limit: int) -> str:
    words = text.split()
    if len(words) <= limit:
        return text
    return " ".join(words[:limit]).rstrip(" ,.;:") + "..."


def _public_signal_title(title: str) -> str:
    payload = _clean_sentence(title)
    if not payload:
        return ""
    payload = re.sub(r"\s+[|\-]\s+[A-Z][A-Za-z0-9&'().,\- ]{1,60}$", "", payload).strip()
    payload = payload.replace("|", " ")
    payload = re.sub(r"\s{2,}", " ", payload).strip(" -|")
    return payload


def _public_signal_titles(run: dict[str, Any], limit: int = 3) -> list[str]:
    titles: list[str] = []
    seen: set[str] = set()
    for item in run.get("trend_signals") or []:
        title = _public_signal_title(str(item.get("title") or ""))
        normalized = title.lower()
        if not title or normalized in seen:
            continue
        seen.add(normalized)
        titles.append(title)
        if len(titles) >= limit:
            break
    return titles


def _hashtags(keywords: list[str], limit: int = 3) -> str:
    items = []
    for keyword in keywords[:limit]:
        tag = re.sub(r"[^A-Za-z0-9]+", "", keyword.title())
        if tag:
            items.append(f"#{tag}")
    return " ".join(items)


def _brand_voice_terms(run: dict[str, Any], limit: int = 3) -> list[str]:
    summary = summarize_brand_profile((run.get("analysis") or {}).get("brand_profile") or "")
    return [
        str(item).strip() for item in (summary.get("core_voice") or [])[:limit] if str(item).strip()
    ]


def _graph_context_terms(run: dict[str, Any], limit: int = 3) -> list[str]:
    collected: list[str] = []
    seen: set[str] = set()
    blocked_prefixes = (
        "hybrid context",
        "article draft about",
        "linkedin draft about",
        "facebook draft about",
        "x draft about",
        "review:",
    )
    for item in run.get("graph_hits") or []:
        label = str(item.get("label") or "").strip()
        kind = str(item.get("kind") or "").strip().lower()
        if label and kind in {"topic"}:
            public_label = re.sub(r"[^A-Za-z0-9]+", " ", label).strip()
            normalized = public_label.lower()
            if public_label and normalized not in seen:
                seen.add(normalized)
                collected.append(public_label)
        for edge in item.get("connections") or []:
            peer_label = str(edge.get("peerLabel") or edge.get("peer") or "").strip()
            if not peer_label:
                continue
            normalized = peer_label.lower()
            if "|" in peer_label or normalized.startswith(blocked_prefixes):
                continue
            if normalized.startswith("202") or normalized.startswith("run:"):
                continue
            if len(peer_label) > 70:
                continue
            public_label = re.sub(r"[^A-Za-z0-9]+", " ", peer_label).strip()
            normalized = public_label.lower()
            if normalized in seen:
                continue
            seen.add(normalized)
            collected.append(public_label)
        if len(collected) >= limit:
            break
    return collected[:limit]


def _voice_phrase(voice_terms: list[str]) -> str:
    lowered = [term.lower() for term in voice_terms if term]
    if not lowered:
        return "practical, evidence-led, and clearly differentiated"
    if len(lowered) == 1:
        return lowered[0]
    if len(lowered) == 2:
        return f"{lowered[0]} and {lowered[1]}"
    return ", ".join(lowered[:-1]) + f", and {lowered[-1]}"


def _hybrid(run: dict[str, Any]) -> dict[str, Any]:
    return (run.get("analysis") or {}).get("hybrid_context") or {}


def _reference_line(run: dict[str, Any]) -> str:
    references = list((_hybrid(run).get("reference_guardrails") or [])[1:])
    if not references:
        return ""
    return "Required references to weave in: " + "; ".join(references) + "."


def build_original_angle(
    run: dict[str, Any], memory_hits: list[dict[str, Any]] | None = None
) -> str:
    article = run.get("article") or {}
    hybrid = _hybrid(run)
    trend_titles = _public_signal_titles(run, limit=3)
    graph_titles = _graph_context_terms(run, limit=3)
    voice_terms = _brand_voice_terms(run, limit=3)
    article_summary = _clean_sentence(
        str(article.get("summary") or ""),
        fallback="The competitor article frames the topic at a surface level.",
    )

    sentences = [
        f"The competitor piece argues that {article_summary[0].lower() + article_summary[1:] if len(article_summary) > 1 else article_summary}",
        "Our response should acknowledge the useful framing, then move the discussion toward operational reliability, approval discipline, and repeatable execution.",
    ]
    if hybrid.get("business_angle"):
        sentences.append(
            f"The business framing should explicitly emphasize {hybrid['business_angle']}."
        )
    if hybrid.get("target_audience"):
        sentences.append(f"The content should feel native to {hybrid['target_audience']}.")
    if trend_titles:
        sentences.append(
            "Recent trend signals reinforce that teams care about "
            + ", ".join(_clean_sentence(title) for title in trend_titles)
            + "."
        )
    if memory_hits:
        sentences.append(
            "Historical Sentinel context suggests readers respond better to concrete operating controls, auditability, and human review than to abstract AI positioning."
        )
    if graph_titles:
        sentences.append(
            "Knowledge graph context reinforces themes around "
            + ", ".join(_clean_sentence(title) for title in graph_titles)
            + "."
        )
    sentences.append(
        "The final position should stay "
        + _voice_phrase(voice_terms)
        + " while remaining clearly differentiated from the competitor's framing."
    )
    if hybrid.get("key_call_to_action"):
        sentences.append(
            f"Close with a CTA that nudges the reader to {hybrid['key_call_to_action']}."
        )
    return " ".join(sentences)


def build_publish_angle(
    run: dict[str, Any], memory_hits: list[dict[str, Any]] | None = None
) -> str:
    hybrid = _hybrid(run)
    trend_titles = _public_signal_titles(run, limit=2)
    graph_titles = _graph_context_terms(run, limit=2)
    voice_terms = _brand_voice_terms(run, limit=3)
    lines = [
        "The stronger move is to define approved AI use, log prompts, keep humans in review, and only publish through a controlled workflow.",
    ]
    if hybrid.get("business_angle"):
        lines.append(f"That matters because {hybrid['business_angle']}.")
    if trend_titles:
        lines.append(
            "The trend signal is clear: "
            + " and ".join(_clean_sentence(title) for title in trend_titles)
            + " both point toward governed execution."
        )
    if memory_hits:
        lines.append("Teams usually fail at the orchestration layer, not at idea generation.")
    if graph_titles:
        lines.append(
            "The operating themes to reinforce are "
            + ", ".join(_clean_sentence(title) for title in graph_titles)
            + "."
        )
    lines.append("Keep the conclusion " + _voice_phrase(voice_terms) + ".")
    return " ".join(lines)


def build_article_draft(
    run: dict[str, Any], memory_hits: list[dict[str, Any]] | None = None
) -> str:
    article = run.get("article") or {}
    hybrid = _hybrid(run)
    competitor_title = _clean_sentence(article.get("title"), fallback="the competitor article")
    competitor_summary = _trim_words(
        _clean_sentence(
            str(article.get("summary") or ""), fallback="It outlines a current market view."
        ),
        70,
    )
    keywords = ", ".join(run.get("keywords") or [])
    trend_titles = _public_signal_titles(run, limit=4)
    trend_block = (
        "\n".join(f"- {title}" for title in trend_titles)
        or "- No external trend signal was captured."
    )
    memory_block = (
        "\n".join(
            f"- {humanize_memory_hit(item)} ({item.get('kind', 'memory')})"
            for item in (memory_hits or [])[:4]
        )
        or "- No historical memory hit was used."
    )
    original_angle = build_original_angle(run, memory_hits)
    strategic_themes = (
        "\n".join(f"- {item}" for item in (hybrid.get("strategic_themes") or [])[:5]) or "- None"
    )
    reference_line = _reference_line(run)

    return f"""# Original Response Article

## Working Title
Why reliable content operations need more than a strong opinion

## Executive Summary
This article responds to **{competitor_title}** with a more operational perspective. Instead of stopping at market commentary, it focuses on repeatability, confidence gating, review discipline, and low-cost execution paths that teams can sustain daily.

## What the competitor gets right
{competitor_summary}

The competitor is directionally useful because it highlights why the topic matters now. That framing is worth keeping. The problem is that most teams fail after the insight stage, not before it. They need a workflow that turns a good idea into a dependable system.

## What the competitor misses
The missing layer is operational design. A useful content engine does not only detect a trend or react to a competitor. It also needs:

- explicit approval checkpoints,
- source-aware synthesis instead of paraphrase,
- platform-specific adaptation,
- observable retries and failure states,
- a zero-cost or near-zero-cost support stack outside the LLM.

## Our original angle
{original_angle}

This is where OpenClaw Content Sentinel is stronger as a production model. It does not treat research, writing, review, image generation, and posting as disconnected tasks. It treats them as one controlled daily loop.

## Trend context worth incorporating
{trend_block}

These signals matter because they show that the conversation is shifting from experimentation toward operational maturity. Teams are not only asking "what should we say?" They are asking "how do we run this every day without adding chaos or cost?"

## Hybrid strategy cues
{strategic_themes}

{reference_line}

## Recommended operating model
1. Start from a concrete daily prompt and one competitor URL.
2. Ingest the source ethically and treat its contents as untrusted data, not instructions.
3. Pull trend context from free sources only.
4. Draft an original long-form response, then adapt it per platform.
5. Generate a visual asset locally or with a self-hosted image backend.
6. Require human approval before any social post.
7. Record evidence, status, and failure reasons for every publish attempt.

## Historical context
{memory_block}

## Conclusion
The real differentiator is not whether a team can generate one good post. It is whether they can do it consistently, safely, and with enough structure to trust the output. That is the gap between thought leadership as a one-off exercise and thought leadership as infrastructure.

## Suggested CTA
{hybrid.get("key_call_to_action") or "If your team is still moving content from tabs and chat drafts into manual posting workflows, start by instrumenting the pipeline itself. Better orchestration will create better content quality over time."}

## Metadata
- Prompt: {run.get("prompt", "").strip()}
- Keywords: {keywords or "None"}
- Source URL: {run.get("competitor_url", "")}
"""


def build_linkedin_draft(
    run: dict[str, Any], memory_hits: list[dict[str, Any]] | None = None
) -> str:
    article = run.get("article") or {}
    hybrid = _hybrid(run)
    competitor_title = _clean_sentence(
        str(article.get("title") or ""), fallback="a competitor article"
    )
    trend_titles = _public_signal_titles(run, limit=2)
    trend_text = ""
    if trend_titles:
        trend_text = (
            "Recent signals like "
            + " and ".join(f"'{title}'" for title in trend_titles)
            + " point the same way.\n\n"
        )
    hashtags = _hashtags(run.get("keywords") or [])
    publish_angle = build_publish_angle(run, memory_hits)
    audience_line = f"For {hybrid.get('target_audience')}," if hybrid.get("target_audience") else ""
    cta = (
        hybrid.get("key_call_to_action")
        or "What is the weakest link in your current content pipeline?"
    )
    return f"""Most teams do not struggle because they lack ideas.

They struggle because their content workflow has no operating model.

{audience_line} we reviewed {competitor_title} and the core takeaway is valid: the topic matters. But insight alone is not enough. Without ingestion discipline, trend validation, approval checkpoints, and platform-specific publishing, good ideas still die in draft form.

{trend_text}The stronger play is to treat content generation like infrastructure:

- one daily prompt
- one competitor source
- zero-cost trend enrichment
- one original article
- channel-specific drafts
- mandatory approval before posting

{publish_angle}

That is how you move from reactive posting to repeatable thought leadership.

{cta}

{hashtags}""".strip()


def build_facebook_draft(
    run: dict[str, Any], memory_hits: list[dict[str, Any]] | None = None
) -> str:
    article = run.get("article") or {}
    hybrid = _hybrid(run)
    competitor_title = _clean_sentence(
        str(article.get("title") or ""), fallback="a recent competitor post"
    )
    trend_title = _clean_sentence(
        str((_public_signal_titles(run, limit=1) or [""])[0]),
        fallback="recent market discussion",
    )
    return f"""A good content engine is not just about writing faster.

It is about building a process you can trust every day.

We used {competitor_title} as the starting point for today's analysis, and the main lesson was simple: strong opinions are not enough without a reliable workflow behind them.

{trend_title} is one more sign that teams now care about consistent execution, not just experimentation.

The better approach is to combine source analysis, trend validation, original synthesis, a visual asset, and human approval before anything gets posted.

That is the difference between posting occasionally and operating a real content system.

{hybrid.get("key_call_to_action") or "How would you tighten the weakest part of your workflow first?"}""".strip()


def build_x_draft(run: dict[str, Any], memory_hits: list[dict[str, Any]] | None = None) -> str:
    article = run.get("article") or {}
    hybrid = _hybrid(run)
    competitor_title = _clean_sentence(
        str(article.get("title") or ""), fallback="a competitor article"
    )
    base = (
        f"We reviewed {competitor_title} today. The real lesson is not the opinion itself, "
        f"it is the workflow behind it: source ingest, free trend research, original synthesis, "
        f"approval gating, then platform-specific posting. Content quality improves when operations improve. "
        f"{hybrid.get('key_call_to_action') or ''}"
    )
    if len(base) <= 280:
        return base
    return base[:277].rstrip() + "..."


def build_image_prompt(run: dict[str, Any], memory_hits: list[dict[str, Any]] | None = None) -> str:
    keywords = ", ".join((run.get("keywords") or [])[:4])
    hybrid = _hybrid(run)
    trend = _clean_sentence(
        cast(str, (_public_signal_titles(run, limit=1) or [""])[0]), fallback="operational clarity"
    )
    brand_summary = summarize_brand_profile((run.get("analysis") or {}).get("brand_profile") or "")
    avoid_terms = ", ".join((brand_summary.get("avoid") or [])[:4])
    graph_terms = ", ".join(_graph_context_terms(run, limit=3))
    return f"""Create a clean editorial illustration for a professional social post about reliable AI content operations.

Visual direction:
- crystalline glass layers
- subtle dashboard panels
- editorial blue and amber accents
- no people unless stylized silhouettes
- emphasis on workflow, approval gates, and signal intelligence

Concept cues:
- competitor insight transformed into original strategy
- trend signal pulse around {trend}
- keywords: {keywords or "content operations, approval workflow"}
- secondary concept anchors: {graph_terms or "workflow orchestration, approval gates, signal intelligence"}
- audience cue: {hybrid.get("target_audience", "") or "operations leaders"}
- business framing: {hybrid.get("business_angle", "") or "operational reliability and approval discipline"}
- avoid visual clichés tied to: {avoid_terms or "hype, exaggerated claims, clickbait"}

Format:
- 1200x630
- suitable for LinkedIn, Facebook, and X
- modern, polished, readable, and brand-safe
"""


def review_drafts(run: dict[str, Any], run_dir: Path) -> dict[str, Any]:
    drafts_dir = run_dir / "drafts"
    article = read_text(drafts_dir / "article.md")
    linkedin = read_text(drafts_dir / "linkedin.md")
    facebook = read_text(drafts_dir / "facebook.md")
    x_post = read_text(drafts_dir / "x.md")
    image_prompt = read_text(drafts_dir / "image_prompt.md")
    trend_titles = [
        item.get("title", "").lower()
        for item in (run.get("trend_signals") or [])[:5]
        if item.get("title")
    ]

    checks = {
        "article_present": len(article.strip()) > 400,
        "linkedin_present": len(linkedin.strip()) > 180,
        "facebook_present": len(facebook.strip()) > 120,
        "x_length_ok": len(x_post.strip()) <= 280,
        "image_prompt_present": len(image_prompt.strip()) > 80,
        "trend_context_present": any(title and title in article.lower() for title in trend_titles)
        if trend_titles
        else True,
        "approval_required": bool(run.get("approval_state") == "pending"),
    }
    issues = [name for name, ok in checks.items() if not ok]
    overall = not issues
    score = 1.0 - min(len(issues) * 0.08, 0.4)
    return {
        "overall_pass": overall,
        "score": round(max(0.4, score), 2),
        "checks": checks,
        "issues": issues,
    }
