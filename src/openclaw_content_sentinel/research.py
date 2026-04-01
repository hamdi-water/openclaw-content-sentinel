from __future__ import annotations

import json
import re
import urllib.parse
from collections import Counter
from typing import cast
from urllib.parse import urljoin
from xml.etree import ElementTree

from .config import AppConfig
from .models import TrendSignal
from .monitoring import monitor
from .utils import fetch_url_with_retry, load_json


def sanitize_html(html: str) -> str:
    """Ref §11.2: Sanitize scraped HTML to prevent prompt injection."""
    if not html:
        return ""
    # Strip script and style blocks
    clean = re.sub(r"<(script|style).*?>.*?</\1>", "", html, flags=re.DOTALL | re.IGNORECASE)
    # Strip all other HTML tags
    clean = re.sub(r"<.*?>", " ", clean)
    # Normalize whitespace
    return re.sub(r"\s+", " ", clean).strip()


try:
    from pytrends.request import TrendReq  # type: ignore[import-untyped]
except Exception:  # pragma: no cover
    TrendReq = None


STOPWORDS = {
    "the",
    "and",
    "for",
    "with",
    "that",
    "this",
    "from",
    "into",
    "your",
    "about",
    "their",
    "will",
    "have",
    "what",
    "when",
    "where",
    "while",
    "agent",
    "content",
}

GENERIC_PROMPT_TERMS = {
    "daily",
    "prompt",
    "template",
    "topic",
    "business",
    "angle",
    "target",
    "audience",
    "mandatory",
    "references",
    "reference",
    "call",
    "action",
    "write",
    "original",
    "response",
    "article",
    "post",
    "targeted",
}

PROMPT_TEMPLATE_LABELS = {
    "topic",
    "business angle",
    "target audience",
    "key call to action",
    "do not say",
    "mandatory references",
}

GOOGLE_NEWS_SEARCH_URL = "https://news.google.com/rss/search?q={query}&hl=en-US&gl=US&ceid=US:en"


def _tokenize(text: str) -> list[str]:
    return re.findall(r"[A-Za-z][A-Za-z0-9\-]{2,}", (text or "").lower())


def _normalize_label(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").strip().rstrip(":").lower())


def _is_template_label(line: str) -> bool:
    return _normalize_label(line) in PROMPT_TEMPLATE_LABELS


def extract_context_terms(
    prompt: str, article_title: str, article_summary: str = "", limit: int = 8
) -> list[str]:
    counts: Counter[str] = Counter()

    for raw_line in (prompt or "").splitlines():
        line = raw_line.strip()
        if not line or _is_template_label(line):
            continue
        for token in _tokenize(line):
            if token in STOPWORDS or token in GENERIC_PROMPT_TERMS:
                continue
            counts[token] += 3

    for token in _tokenize(article_title):
        if token in STOPWORDS or token in GENERIC_PROMPT_TERMS:
            continue
        counts[token] += 2

    for token in _tokenize(article_summary)[:80]:
        if token in STOPWORDS or token in GENERIC_PROMPT_TERMS:
            continue
        counts[token] += 1

    return [word for word, _ in counts.most_common(limit)]


def prompt_is_actionable(prompt: str) -> bool:
    lines = [line.strip() for line in (prompt or "").splitlines() if line.strip()]
    if not lines:
        return False
    non_label_lines = [line for line in lines if not _is_template_label(line)]
    if not non_label_lines:
        return False
    contextual_terms = extract_context_terms(prompt, "", "", limit=12)
    freeform_token_count = sum(len(_tokenize(line)) for line in non_label_lines)
    return bool(contextual_terms) and freeform_token_count >= 4


def _signal_relevance_score(signal: TrendSignal, context_terms: list[str]) -> int:
    if not context_terms:
        return 0
    haystack = f"{signal.title} {signal.snippet}".lower()
    return sum(1 for term in context_terms if term and term in haystack)


def derive_keywords(
    prompt: str, article_title: str, article_summary: str = "", limit: int = 6
) -> list[str]:
    keywords = extract_context_terms(prompt, article_title, article_summary, limit=limit)
    if keywords:
        return keywords
    text = f"{prompt} {article_title} {article_summary}"
    words = _tokenize(text)
    counts = Counter(
        word for word in words if word not in STOPWORDS and word not in GENERIC_PROMPT_TERMS
    )
    return [word for word, _ in counts.most_common(limit)]


def load_feed_urls(config: AppConfig) -> list[str]:
    payload = load_json(config.feeds_file, default={"feeds": []})
    return cast(list[str], payload.get("feeds", []))


def fetch_rss_signals(
    config: AppConfig, keywords: list[str], limit_per_feed: int = 4
) -> list[TrendSignal]:
    results: list[TrendSignal] = []
    for feed_url in load_feed_urls(config):
        try:
            body = fetch_url_with_retry(
                feed_url, timeout=config.http_timeout, worker_id=config.worker_id
            )
        except Exception as exc:
            monitor.record_scraping_error(source=feed_url, error_type=type(exc).__name__)
            monitor.export()
            continue
        try:
            root = ElementTree.fromstring(body)  # noqa: S314
        except ElementTree.ParseError:
            monitor.record_scraping_error(source=feed_url, error_type="ParseError")
            monitor.export()
            continue
        items = root.findall(".//item") or root.findall(".//entry")
        added = 0
        for item in items:
            title = (item.findtext("title") or "").strip()
            link = (
                item.findtext("link") or item.findtext("{http://www.w3.org/2005/Atom}link") or ""
            ).strip()
            description = (item.findtext("description") or item.findtext("summary") or "").strip()
            haystack = f"{title} {description}".lower()
            score = sum(1 for keyword in keywords if keyword.lower() in haystack)
            if score == 0 and keywords:
                continue
            results.append(
                TrendSignal(
                    source=feed_url,
                    title=title or "Untitled feed item",
                    url=link,
                    snippet=description[:280],
                    published_at=(
                        item.findtext("pubDate") or item.findtext("updated") or ""
                    ).strip(),
                    score=score,
                )
            )
            added += 1
            if added >= limit_per_feed:
                break
    return sorted(results, key=lambda item: item.score, reverse=True)


def fetch_public_news_signals(
    config: AppConfig, keywords: list[str], limit_per_keyword: int = 3
) -> list[TrendSignal]:
    if not config.enable_public_news or not keywords:
        return []

    signals: list[TrendSignal] = []
    seen: set[str] = set()
    for keyword in keywords[:4]:
        query = urllib.parse.quote_plus(keyword)
        feed_url = GOOGLE_NEWS_SEARCH_URL.format(query=query)
        try:
            body = fetch_url_with_retry(
                feed_url, timeout=config.http_timeout, worker_id=config.worker_id
            )
        except Exception as exc:
            monitor.record_scraping_error(source="google-news-rss", error_type=type(exc).__name__)
            monitor.export()
            continue
        try:
            root = ElementTree.fromstring(body)  # noqa: S314
        except ElementTree.ParseError:
            monitor.record_scraping_error(source="google-news-rss", error_type="ParseError")
            monitor.export()
            continue

        added = 0
        for item in root.findall(".//item"):
            title = (item.findtext("title") or "").strip()
            link = urljoin(feed_url, (item.findtext("link") or "").strip())
            description = (item.findtext("description") or "").strip()
            if not title or not link:
                continue
            dedupe_key = f"{title.lower()}::{link.lower()}"
            if dedupe_key in seen:
                continue
            seen.add(dedupe_key)
            haystack = f"{title} {description}".lower()
            score = max(1, sum(1 for token in keywords if token.lower() in haystack))
            signals.append(
                TrendSignal(
                    source="google-news-rss",
                    title=title,
                    url=link,
                    snippet=description[:280],
                    published_at=(item.findtext("pubDate") or "").strip(),
                    score=score,
                )
            )
            added += 1
            if added >= limit_per_keyword:
                break
    return sorted(signals, key=lambda item: item.score, reverse=True)


def fetch_google_trends_signals(
    config: AppConfig, keywords: list[str], limit_per_keyword: int = 4
) -> list[TrendSignal]:
    if not config.enable_pytrends or TrendReq is None or not keywords:
        return []
    try:
        client = TrendReq(hl="en-US", tz=0)
    except Exception:
        return []

    signals: list[TrendSignal] = []
    for keyword in keywords[:4]:
        try:
            client.build_payload([keyword], timeframe="now 7-d")
            related = client.related_queries().get(keyword) or {}
        except Exception:  # noqa: S112
            # Skip failed pytrends keywords to avoid blocking the whole pipeline
            continue

        datasets = []
        for label in ("top", "rising"):
            dataset = related.get(label)
            if dataset is not None:
                datasets.append((label, dataset))

        added = 0
        seen: set[str] = set()
        for label, dataset in datasets:
            try:
                records = dataset.head(limit_per_keyword).to_dict("records")
            except Exception:  # noqa: S112
                # Skip failed dataset exports or conversions
                continue
            for row in records:
                title = str(row.get("query") or "").strip()
                if not title:
                    continue
                key = title.lower()
                if key in seen:
                    continue
                seen.add(key)
                value = row.get("value", 0)
                try:
                    numeric_value = int(value)
                except Exception:
                    numeric_value = 0
                signals.append(
                    TrendSignal(
                        source="google-trends",
                        title=title,
                        url=f"https://trends.google.com/trends/explore?q={urllib.parse.quote(title)}",
                        snippet=f"Related {label} query for '{keyword}'",
                        score=max(1, min(10, numeric_value // 10 if numeric_value else 1)),
                    )
                )
                added += 1
                if added >= limit_per_keyword:
                    break
            if added >= limit_per_keyword:
                break
    return sorted(signals, key=lambda item: item.score, reverse=True)


def query_searx(config: AppConfig, keywords: list[str], limit: int = 8) -> list[TrendSignal]:
    if not config.searx_url or not keywords:
        return []
    query = " ".join(keywords)
    encoded = urllib.parse.urlencode({"q": query, "format": "json"})
    url = f"{config.searx_url.rstrip('/')}/search?{encoded}"
    try:
        body = fetch_url_with_retry(url, timeout=config.http_timeout, worker_id=config.worker_id)
        payload = json.loads(body.decode("utf-8", errors="replace"))
    except Exception as exc:
        monitor.record_scraping_error(source="searxng", error_type=type(exc).__name__)
        monitor.export()
        return []
    signals: list[TrendSignal] = []
    for result in payload.get("results", [])[:limit]:
        title = result.get("title", "").strip()
        content = result.get("content", "").strip()
        haystack = f"{title} {content}".lower()
        score = sum(1 for keyword in keywords if keyword.lower() in haystack)
        signals.append(
            TrendSignal(
                source="searxng",
                title=title or "Untitled search result",
                url=result.get("url", "").strip(),
                snippet=content[:280],
                published_at=result.get("publishedDate", "").strip(),
                score=score,
            )
        )
    return sorted(signals, key=lambda item: item.score, reverse=True)


def curate_trend_signals(
    signals: list[TrendSignal], context_terms: list[str], limit: int = 18
) -> list[TrendSignal]:
    if not signals:
        return []
    deduped: dict[str, tuple[int, int, TrendSignal]] = {}
    for signal in signals:
        dedupe_key = (
            f"{signal.source.lower()}::{signal.title.strip().lower()}::{signal.url.strip().lower()}"
        )
        relevance = _signal_relevance_score(signal, context_terms)
        if context_terms and relevance <= 0:
            continue
        adjusted_score = int(signal.score) + (relevance * 2)
        candidate = TrendSignal(
            source=signal.source,
            title=signal.title,
            url=signal.url,
            snippet=signal.snippet,
            published_at=signal.published_at,
            score=max(1, min(10, adjusted_score)),
        )
        current = deduped.get(dedupe_key)
        if current is None or (relevance, candidate.score) > (current[0], current[1]):
            deduped[dedupe_key] = (relevance, candidate.score, candidate)
    ranked = sorted(
        deduped.values(), key=lambda item: (item[0], item[1], item[2].title.lower()), reverse=True
    )
    return [item[2] for item in ranked[:limit]]
