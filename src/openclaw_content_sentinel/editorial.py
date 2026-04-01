# ruff: noqa: E501
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast
from urllib.parse import urlparse

from .config import AppConfig
from .research import prompt_is_actionable
from .utils import read_text

DEFAULT_BRAND_PROFILE = """# Brand Profile

## Core voice

- Clear
- Practical
- Credible
- Insightful
- Original

## Avoid

- hype without evidence
- copied phrasing from the competitor source
- exaggerated claims
- clickbait headlines

## House rules

- Always cite or attribute the competitor source when responding to a public article.
- Prefer synthesis and critique over paraphrase.
- Keep LinkedIn long-form, Facebook conversational, and X concise.
"""

GENERIC_HYPE_PATTERNS = [
    "game changing",
    "revolutionary",
    "must read",
    "secret to",
    "guaranteed",
    "dominate",
    "unbelievable",
    "viral",
    "best ever",
]

SOURCE_INJECTION_PATTERNS = [
    "ignore previous instructions",
    "ignore all previous instructions",
    "disregard previous instructions",
    "system prompt",
    "developer message",
    "hidden prompt",
    "you are chatgpt",
    "as an ai assistant",
    "repeat the prompt",
    "do not mention these instructions",
]

CTA_PATTERNS = [
    "what is",
    "what's",
    "tell us",
    "share your",
    "start by",
    "learn more",
    "want to",
    "if your team",
    "how are you",
]


@dataclass(frozen=True)
class BrandProfile:
    core_voice: list[str]
    avoid: list[str]
    house_rules: list[str]
    raw_text: str
    language: str = "fr"  # §7: Default language


def load_brand_profile_text(config: AppConfig) -> str:
    path = Path(config.brand_profile_file)
    if path.exists():
        payload = read_text(path).strip()
        if payload:
            return payload
    return DEFAULT_BRAND_PROFILE


def parse_brand_profile(payload: str, persona_id: str = "default") -> BrandProfile:
    current = ""
    sections: dict[str, list[str]] = {
        "core voice": [],
        "avoid": [],
        "house rules": [],
    }
    for raw_line in (payload or "").splitlines():
        line = raw_line.strip()
        if not line:
            continue
        lowered = line.lower()
        if lowered.startswith("## persona:"):
            continue

        if line.startswith("## "):
            current = line[3:].strip().lower()
            continue

        # If we are in the default section or in the matching persona block
        if not lowered.startswith("## persona:"):
            if line.startswith("- ") and current in sections:
                sections[current].append(line[2:].strip())

    return BrandProfile(
        core_voice=sections["core voice"],
        avoid=sections["avoid"],
        house_rules=sections["house rules"],
        raw_text=(payload or DEFAULT_BRAND_PROFILE).strip(),
    )


def summarize_brand_profile(
    profile_text: str | dict[str, Any] | BrandProfile,
    limit: int = 8,
    persona_id: str = "default",
) -> dict[str, Any]:
    if isinstance(profile_text, BrandProfile):
        profile = profile_text
    elif isinstance(profile_text, dict):
        profile = BrandProfile(
            core_voice=[
                str(item).strip()
                for item in (profile_text.get("core_voice") or [])
                if str(item).strip()
            ],
            avoid=[
                str(item).strip() for item in (profile_text.get("avoid") or []) if str(item).strip()
            ],
            house_rules=[
                str(item).strip()
                for item in (profile_text.get("house_rules") or [])
                if str(item).strip()
            ],
            raw_text=DEFAULT_BRAND_PROFILE,
        )
    else:
        profile = parse_brand_profile(profile_text, persona_id=persona_id)
    return {
        "core_voice": profile.core_voice[:limit],
        "avoid": profile.avoid[:limit],
        "house_rules": profile.house_rules[:limit],
    }


def analyze_source_safety(article: dict[str, Any]) -> dict[str, Any]:
    title = str(article.get("title") or "").strip()
    clean_text = str(article.get("clean_text") or "")
    headings = "\n".join(cast(list[str], article.get("headings") or []))
    haystack = f"{title}\n{headings}\n{clean_text}".lower()
    flags = [pattern for pattern in SOURCE_INJECTION_PATTERNS if pattern in haystack]
    severity = "high" if flags else "low"
    return {
        "severity": severity,
        "flags": flags,
        "trusted_as_content_only": True,
        "recommended_action": (
            "Treat the competitor page as untrusted content. Use it only for "
            "factual context and never as an instruction source."
        ),
    }


def render_source_safety_report(run: dict[str, Any]) -> str:
    source_safety = cast(dict[str, Any], (run.get("analysis") or {}).get("source_safety") or {})
    flags = cast(list[str], source_safety.get("flags") or [])
    lines = [
        "# Source Safety",
        "",
        f"Severity: {source_safety.get('severity', 'unknown')}",
        f"Trusted as content only: {source_safety.get('trusted_as_content_only', False)}",
        "",
        "Flags:",
    ]
    if flags:
        lines.extend(f"- {item}" for item in flags)
    else:
        lines.append("- None detected")
    lines.extend(
        [
            "",
            "Recommended action:",
            "",
            source_safety.get(
                "recommended_action",
                "Treat external content as untrusted and preserve human approval "
                "before publishing.",
            ),
            "",
        ]
    )
    return "\n".join(lines)


def _normalize_text(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").strip().lower())


def _tokenize(text: str) -> list[str]:
    return re.findall(r"[a-z0-9][a-z0-9\-]{1,}", _normalize_text(text))


def _ngram_set(text: str, n: int = 3, limit: int = 4000) -> set[str]:
    tokens = _tokenize(text)[:limit]
    if len(tokens) < n:
        return set()
    return {" ".join(tokens[index : index + n]) for index in range(0, len(tokens) - n + 1)}


def _jaccard(left: set[str], right: set[str]) -> float:
    if not left or not right:
        return 0.0
    union = left | right
    if not union:
        return 0.0
    return len(left & right) / len(union)


def similarity_ratio(left: str, right: str) -> float:
    left_tokens = set(_tokenize(left))
    right_tokens = set(_tokenize(right))
    token_score = _jaccard(left_tokens, right_tokens)
    ngram_score = _jaccard(_ngram_set(left), _ngram_set(right))
    return round((token_score * 0.4) + (ngram_score * 0.6), 4)


def _hashtags_count(text: str) -> int:
    return len(re.findall(r"(?<!\w)#\w+", text or ""))


def _has_cta(text: str) -> bool:
    lowered = _normalize_text(text)
    return "?" in (text or "") or any(pattern in lowered for pattern in CTA_PATTERNS)


def _contains_any_phrase(text: str, phrases: list[str]) -> list[str]:
    lowered = _normalize_text(text)
    matches = []
    for phrase in phrases:
        candidate = _normalize_text(phrase)
        if candidate and candidate in lowered:
            matches.append(phrase)
    return matches


def _strip_article_metadata(text: str) -> str:
    payload = text or ""
    marker = "\n## Metadata"
    if marker in payload:
        payload = payload.split(marker, 1)[0]
    return payload.strip()


def _image_prompt_has_disallowed_terms(text: str, phrases: list[str]) -> list[str]:
    matches: list[str] = []
    for raw_line in (text or "").splitlines():
        line = _normalize_text(raw_line)
        if not line:
            continue
        negated = any(
            marker in line for marker in ("avoid", "without", "exclude", "do not", "dont", "no ")
        )
        for phrase in phrases:
            candidate = _normalize_text(phrase)
            if candidate and candidate in line and not negated:
                matches.append(phrase)
    deduped: list[str] = []
    seen: set[str] = set()
    for item in matches:
        lowered = item.lower()
        if lowered in seen:
            continue
        seen.add(lowered)
        deduped.append(item)
    return deduped


def _references_present(text: str, references: list[str]) -> bool:
    if not references:
        return True
    lowered = _normalize_text(text)
    hits = 0
    for reference in references:
        candidate = _normalize_text(reference)
        if candidate and candidate in lowered:
            hits += 1
    return hits >= min(1, len(references))


def _core_angle_terms(original_angle: str) -> list[str]:
    stopwords = {
        "the",
        "and",
        "for",
        "with",
        "that",
        "this",
        "from",
        "then",
        "into",
        "they",
        "them",
        "their",
        "should",
        "would",
        "where",
        "toward",
        "around",
        "about",
    }
    counts: dict[str, int] = {}
    for token in _tokenize(original_angle):
        if token in stopwords or len(token) < 5:
            continue
        counts[token] = counts.get(token, 0) + 1
    return [item for item, _ in sorted(counts.items(), key=lambda pair: (-pair[1], pair[0]))[:6]]


def _contains_angle_terms(article_text: str, original_angle: str) -> bool:
    terms = _core_angle_terms(original_angle)
    if not terms:
        return False
    lowered = _normalize_text(article_text)
    return sum(1 for term in terms if term in lowered) >= min(2, len(terms))


def _has_attribution(text: str, competitor_url: str, competitor_title: str) -> bool:
    lowered = _normalize_text(text)
    domain = urlparse(competitor_url).netloc.lower().replace("www.", "")
    title_bits = [token for token in _tokenize(competitor_title) if len(token) >= 5][:4]
    domain_hit = domain and domain in lowered
    title_hit = bool(title_bits) and sum(1 for token in title_bits if token in lowered) >= min(
        2, len(title_bits)
    )
    generic_hit = any(
        marker in lowered
        for marker in ("competitor", "source article", "source piece", "according to")
    )
    return bool(domain_hit or title_hit or generic_hit)


def evaluate_run_quality(config: AppConfig, run: dict[str, Any], run_dir: Path) -> dict[str, Any]:
    drafts_dir = run_dir / "drafts"
    article_draft = read_text(drafts_dir / "article.md").strip()
    linkedin = read_text(drafts_dir / "linkedin.md").strip()
    facebook = read_text(drafts_dir / "facebook.md").strip()
    x_post = read_text(drafts_dir / "x.md").strip()
    image_prompt = read_text(drafts_dir / "image_prompt.md").strip()
    article_body = _strip_article_metadata(article_draft)
    review_corpus = "\n".join([article_body, linkedin, facebook, x_post]).strip()
    image_prompt_disallowed_hits = _image_prompt_has_disallowed_terms(
        image_prompt,
        ["clickbait", "shock tactic", "nsfw", "violent"],
    )
    article = cast(dict[str, Any], run.get("article") or {})
    competitor_text = str(article.get("clean_text") or "")
    competitor_title = str(article.get("title") or "")
    source_safety = cast(
        dict[str, Any],
        (run.get("analysis") or {}).get("source_safety") or analyze_source_safety(article),
    )
    daily_input = cast(dict[str, Any], run.get("daily_input") or {})
    identity = run.get("identity") or {}
    persona_id = str(identity.get("persona_id") or "default")
    brand_summary = summarize_brand_profile(
        cast(str, (run.get("analysis") or {}).get("brand_profile", "")),
        persona_id=persona_id,
    )
    brand_avoid_terms = list(cast(list[str], brand_summary.get("avoid") or []))
    generic_avoid_hits = _contains_any_phrase(review_corpus, GENERIC_HYPE_PATTERNS)
    brand_avoid_hits = _contains_any_phrase(review_corpus, brand_avoid_terms)
    forbidden_phrase_hits = _contains_any_phrase(
        review_corpus, list(cast(list[str], daily_input.get("do_not_say") or []))
    )
    mandatory_references = list(cast(list[str], daily_input.get("mandatory_references") or []))
    competitor_overlap = similarity_ratio(article_draft, competitor_text)
    memory_candidates = [
        str(item.get("text") or "")
        for item in cast(list[dict[str, Any]], run.get("memory_hits") or [])
        if item.get("text")
    ]
    historical_overlap = max(
        (similarity_ratio(article_draft, text) for text in memory_candidates), default=0.0
    )
    trend_titles = [
        str(item.get("title") or "").strip()
        for item in cast(list[dict[str, Any]], run.get("trend_signals") or [])[:6]
        if item.get("title")
    ]
    _draft_corpus_normalized = _normalize_text(
        "\n".join([article_draft, linkedin, facebook, x_post])
    )

    def _trend_hit(title: str) -> bool:
        if not title:
            return False
        if _normalize_text(title) in _draft_corpus_normalized:
            return True
        core = re.sub(r"\s*[-|]\s*[A-Z][^-|]{2,40}$", "", title).strip()
        if core and len(core) >= 10 and _normalize_text(core) in _draft_corpus_normalized:
            return True
        words = [w for w in _tokenize(title) if len(w) >= 5]
        if len(words) >= 2 and sum(1 for w in words if w in _draft_corpus_normalized) >= 2:
            return True
        return False

    trend_hits = sum(1 for title in trend_titles if _trend_hit(title))
    original_angle = str((run.get("analysis") or {}).get("original_angle") or "")
    portfolio_diversity = cast(
        dict[str, Any], (run.get("analysis") or {}).get("portfolio_diversity") or {}
    )
    recent_similarity_max = float(portfolio_diversity.get("recent_similarity_max", 0.0) or 0.0)

    checks = {
        "prompt_actionable": prompt_is_actionable(str(run.get("prompt", ""))),
        "article_present": len(article_draft) > 400,
        "article_attributed": _has_attribution(
            article_draft, str(run.get("competitor_url", "")), competitor_title
        ),
        "mandatory_references_present": _references_present(
            "\n".join([article_draft, linkedin, facebook, x_post]), mandatory_references
        ),
        "trend_signal_count_sufficient": len(trend_titles) >= 1,
        "trend_context_present": trend_hits > 0 if trend_titles else False,
        "article_original_enough": competitor_overlap <= config.competitor_overlap_threshold,
        "history_original_enough": historical_overlap <= config.memory_overlap_threshold,
        "recent_topic_not_repetitive": recent_similarity_max < 0.75,
        "source_safe": source_safety.get("severity") != "high",
        "brand_safe": not brand_avoid_hits and not generic_avoid_hits and not forbidden_phrase_hits,
        "cta_present": _has_cta(linkedin) or _has_cta(facebook) or _has_cta(article_draft),
        "linkedin_platform_fit": len(linkedin) >= 180 and _hashtags_count(linkedin) <= 5,
        "facebook_platform_fit": len(facebook) >= 120 and _hashtags_count(facebook) <= 5,
        "x_platform_fit": len(x_post) <= 280 and _hashtags_count(x_post) <= 4,
        "image_prompt_brand_safe": not image_prompt_disallowed_hits,
        "original_angle_present": _contains_angle_terms(article_draft, original_angle),
    }

    weights = {
        "prompt_actionable": 0.08,
        "article_present": 0.12,
        "article_attributed": 0.08,
        "mandatory_references_present": 0.05,
        "trend_signal_count_sufficient": 0.04,
        "trend_context_present": 0.04,
        "article_original_enough": 0.16,
        "history_original_enough": 0.08,
        "recent_topic_not_repetitive": 0.06,
        "source_safe": 0.12,
        "brand_safe": 0.10,
        "cta_present": 0.05,
        "linkedin_platform_fit": 0.05,
        "facebook_platform_fit": 0.04,
        "x_platform_fit": 0.04,
        "image_prompt_brand_safe": 0.03,
        "original_angle_present": 0.05,
    }

    issues = [name for name, ok in checks.items() if not ok]
    remediation_map = {
        "prompt_actionable": "Replace the placeholder daily prompt or promote a ready queued brief.",
        "article_present": "Expand the article so it contains a real argument.",
        "article_attributed": "Add explicit attribution to the competitor source or domain.",
        "mandatory_references_present": "Add the required references from the daily brief.",
        "trend_signal_count_sufficient": "Collect at least one validated trend signal.",
        "trend_context_present": "Use at least one validated trend signal inside the article.",
        "article_original_enough": "Rewrite the article body to reduce paraphrase.",
        "history_original_enough": "Refresh the framing to avoid repeating historical scripts.",
        "recent_topic_not_repetitive": "Change the angle or topic framing.",
        "source_safe": "Treat competitor content strictly as untrusted source material.",
        "brand_safe": "Remove hype, clickbait, or forbidden phrases.",
        "cta_present": "Add a concrete operator CTA or closing question.",
        "linkedin_platform_fit": "Adjust LinkedIn draft length or hashtag density.",
        "facebook_platform_fit": "Adjust Facebook draft length or hashtag density.",
        "x_platform_fit": "Trim X draft below 280 chars.",
        "image_prompt_brand_safe": "Make image prompt more editorial and brand-safe.",
        "original_angle_present": "Strengthen the distinct operating angle.",
    }
    remediation = [remediation_map[item] for item in issues]
    blockers = [
        item
        for item in issues
        if item
        in {
            "article_present",
            "article_attributed",
            "mandatory_references_present",
            "prompt_actionable",
            "trend_signal_count_sufficient",
            "trend_context_present",
            "article_original_enough",
            "history_original_enough",
            "recent_topic_not_repetitive",
            "source_safe",
            "brand_safe",
            "linkedin_platform_fit",
            "facebook_platform_fit",
            "x_platform_fit",
        }
    ]

    score = round(sum(weight for name, weight in weights.items() if checks.get(name)), 2)
    overall_pass = not blockers

    return {
        "overall_pass": overall_pass,
        "score": score,
        "checks": checks,
        "issues": issues,
        "publish_blockers": blockers,
        "remediation": remediation,
        "metrics": {
            "competitor_overlap_ratio": competitor_overlap,
            "historical_overlap_ratio": historical_overlap,
            "recent_similarity_max": recent_similarity_max,
            "trend_titles_used": trend_hits,
            "trend_titles_available": len(trend_titles),
            "brand_avoid_hits": brand_avoid_hits,
            "generic_hype_hits": generic_avoid_hits,
            "forbidden_phrase_hits": forbidden_phrase_hits,
            "mandatory_references": mandatory_references,
            "source_safety_severity": source_safety.get("severity", "unknown"),
            "source_safety_flags": list(source_safety.get("flags") or []),
            "prompt_actionable": checks["prompt_actionable"],
            "trend_research_context_terms": list(
                ((run.get("analysis") or {}).get("trend_research") or {}).get("context_terms") or []
            ),
            "recent_similar_runs": list(portfolio_diversity.get("recent_similar_runs") or []),
            "hashtags": {
                "linkedin": _hashtags_count(linkedin),
                "facebook": _hashtags_count(facebook),
                "x": _hashtags_count(x_post),
            },
            "lengths": {
                "article": len(article_draft),
                "linkedin": len(linkedin),
                "facebook": len(facebook),
                "x": len(x_post),
                "image_prompt": len(image_prompt),
            },
        },
        "brand_profile": brand_summary,
        "source_safety": source_safety,
    }
