# ruff: noqa: E501
from __future__ import annotations

import base64
import html
import json
import re
import shutil
import subprocess
from html.parser import HTMLParser
from typing import Any, cast
from urllib.parse import urljoin

from .config import AppConfig
from .identity import canonicalize_url, sha256_text
from .models import CompetitorArticle
from .utils import fetch_url_with_retry

try:
    from bs4 import BeautifulSoup
    HAS_BS4 = True
except ImportError:  # pragma: no cover
    HAS_BS4 = False
    BeautifulSoup = None  # type: ignore[assignment]


OPEN_RESULT_ID_RE = re.compile(r"id:\s*([A-Z0-9]+)", re.IGNORECASE)


class _Stripper(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.parts: list[str] = []

    def handle_data(self, data: str) -> None:
        text = data.strip()
        if text:
            self.parts.append(text)

    def get_text(self) -> str:
        return " ".join(self.parts)


def fetch_html(url: str, config: AppConfig) -> str:
    body = fetch_url_with_retry(url, timeout=config.http_timeout, worker_id=config.worker_id)
    return body.decode("utf-8", errors="replace")


def _normalize_whitespace(value: str) -> str:
    return re.sub(r"\s+", " ", (value or "").strip())


def _extract_ld_json_blocks(soup: BeautifulSoup) -> list[dict[str, Any]]:
    blocks: list[dict[str, Any]] = []
    for node in soup.find_all("script", attrs={"type": "application/ld+json"}):
        raw = (node.string or node.get_text() or "").strip()
        if not raw:
            continue
        try:
            payload = json.loads(raw)
        except Exception as exc:
            import logging

            logging.debug(f"Ignoring malformed LD+JSON block: {exc}")
            continue
        if isinstance(payload, list):
            blocks.extend(item for item in payload if isinstance(item, dict))
        elif isinstance(payload, dict):
            if isinstance(payload.get("@graph"), list):
                blocks.extend(item for item in payload["@graph"] if isinstance(item, dict))
            blocks.append(payload)
    return blocks


def _select_article_ld_json(blocks: list[dict[str, Any]]) -> dict[str, Any]:
    for block in blocks:
        types = block.get("@type")
        if isinstance(types, list):
            type_names = {str(item).lower() for item in types}
        else:
            type_names = {str(types).lower()}
        if {"newsarticle", "article", "reportage", "blogposting"} & type_names:
            return block
    return {}


def _author_from_ld_json(block: dict[str, Any]) -> str:
    author = block.get("author")
    if isinstance(author, list):
        names = []
        for item in author:
            if isinstance(item, dict):
                name = _normalize_whitespace(str(item.get("name") or ""))
                if name:
                    names.append(name)
        return ", ".join(names)
    if isinstance(author, dict):
        return _normalize_whitespace(str(author.get("name") or ""))
    return _normalize_whitespace(str(author or ""))


def _published_from_soup(soup: BeautifulSoup, article_ld: dict[str, Any]) -> str:
    published = _normalize_whitespace(str(article_ld.get("datePublished") or ""))
    if not published:
        for attrs in ({"property": "article:published_time"}, {"name": "pubdate"}):
            node = soup.find("meta", attrs=cast(Any, attrs))
            if node and node.get("content"):
                published = _normalize_whitespace(str(node.get("content") or ""))
                break
    if published:
        return published
    time_node = soup.find("time")
    if time_node:
        return _normalize_whitespace(
            str(time_node.get("datetime") or time_node.get_text(" ", strip=True) or "")
        )
    return ""


def _author_from_soup(soup: BeautifulSoup, article_ld: dict[str, Any]) -> str:
    author = _author_from_ld_json(article_ld)
    if author:
        return author
    for attrs in (
        {"name": "author"},
        {"property": "article:author"},
        {"name": "parsely-author"},
    ):
        node = soup.find("meta", attrs=cast(Any, attrs))
        if node and node.get("content"):
            return _normalize_whitespace(str(node.get("content") or ""))
    return ""


def _browser_fallback_html(url: str, config: AppConfig) -> str:
    executable = shutil.which(config.openclaw_bin) or config.openclaw_bin

    def _run(*args: str) -> str:
        result = subprocess.run(  # noqa: S603
            [
                executable,
                "browser",
                "--browser-profile",
                config.browser_profile_scraper,
                "--timeout",
                str(config.browser_timeout_ms),
                *args,
            ],
            cwd=config.base_dir,
            capture_output=True,
            text=True,
            timeout=max(30, int(config.browser_timeout_ms / 1000) + 20),
            check=False,
            shell=False,
        )
        stdout = (result.stdout or "").strip()
        stderr = (result.stderr or "").strip()
        if result.returncode != 0:
            raise RuntimeError(stderr or stdout or f"OpenClaw browser failed: {' '.join(args)}")
        return stdout

    _run("start")
    opened = _run("open", url)
    match = OPEN_RESULT_ID_RE.search(opened)
    if not match:
        raise RuntimeError(f"Unable to parse target id from browser open output: {opened}")
    target_id = match.group(1)
    _run("wait", "--load", "domcontentloaded", "--target-id", target_id)
    _run("wait", "--time", "1500", "--target-id", target_id)
    encoded = (
        _run(
            "evaluate",
            "--fn",
            "() => btoa(unescape(encodeURIComponent(document.documentElement.outerHTML)))",
            "--target-id",
            target_id,
        )
        .strip()
        .strip('"')
    )
    if not encoded:
        return ""
    return base64.b64decode(encoded.encode("utf-8")).decode("utf-8", errors="replace")


def _article_extraction_is_weak(article: CompetitorArticle) -> bool:
    paragraph_count = len([part for part in article.clean_text.split("\n\n") if part.strip()])
    return len(article.clean_text) < 1200 or paragraph_count < 4 or len(article.headings) < 2


def _extract_with_bs4(html_text: str, url: str) -> CompetitorArticle:
    soup = BeautifulSoup(html_text, "html.parser")
    ld_json_blocks = _extract_ld_json_blocks(soup)
    article_ld = _select_article_ld_json(ld_json_blocks)
    meta_title = soup.find("meta", attrs={"property": "og:title"})
    title = (
        _normalize_whitespace(str(meta_title.get("content") or ""))
        if meta_title and meta_title.get("content")
        else ""
    )
    if not title:
        title = _normalize_whitespace(str(article_ld.get("headline") or ""))
    if not title:
        title = soup.title.get_text(" ", strip=True) if soup.title else url
    canonical_node = soup.find(
        "link", attrs=cast(Any, {"rel": lambda value: value and "canonical" in str(value).lower()})
    )
    canonical_url = (
        canonicalize_url(urljoin(url, cast(str, canonical_node.get("href"))))
        if canonical_node and canonical_node.get("href")
        else canonicalize_url(url)
    )
    article_node = soup.find("article") or soup.body or soup
    headings = []
    seen_headings: set[str] = set()
    for node in article_node.find_all(["h1", "h2", "h3"]):
        heading = _normalize_whitespace(node.get_text(" ", strip=True))
        if heading and heading.lower() not in seen_headings:
            seen_headings.add(heading.lower())
            headings.append(heading)
        if len(headings) >= 12:
            break
    paragraphs = [
        _normalize_whitespace(node.get_text(" ", strip=True)) for node in article_node.find_all("p")
    ]
    clean_text = "\n\n".join(p for p in paragraphs if len(p.split()) > 6)
    if len(clean_text) < 900 and article_ld.get("articleBody"):
        clean_text = _normalize_whitespace(str(article_ld.get("articleBody") or ""))
    links = []
    for link in article_node.find_all("a", href=True):
        href = urljoin(url, cast(str, link["href"]))
        if href not in links:
            links.append(href)
        if len(links) >= 30:
            break
    description_node = soup.find("meta", attrs={"name": "description"}) or soup.find(
        "meta", attrs={"property": "og:description"}
    )
    description = (
        _normalize_whitespace(str(description_node.get("content") or ""))
        if description_node and description_node.get("content")
        else ""
    )
    summary = (description or clean_text[:1200]).strip()
    return CompetitorArticle(
        url=url,
        title=title,
        canonical_url=canonical_url,
        author=_author_from_soup(soup, article_ld),
        published_at=_published_from_soup(soup, article_ld),
        clean_text=clean_text,
        headings=headings,
        links=links,
        summary=summary,
        raw_html_hash=sha256_text(html_text),
        content_hash=sha256_text(clean_text),
    )


def _extract_without_bs4(html_text: str, url: str) -> CompetitorArticle:
    title_match = re.search(r"<title[^>]*>(.*?)</title>", html_text, re.IGNORECASE | re.DOTALL)
    title = html.unescape(title_match.group(1).strip()) if title_match else url
    paragraphs = re.findall(r"<p[^>]*>(.*?)</p>", html_text, re.IGNORECASE | re.DOTALL)
    cleaned = []
    for paragraph in paragraphs:
        stripper = _Stripper()
        stripper.feed(paragraph)
        text = stripper.get_text().strip()
        if len(text.split()) > 6:
            cleaned.append(text)
    clean_text = "\n\n".join(cleaned)
    headings = []
    for tag in ["h1", "h2", "h3"]:
        for value in re.findall(
            rf"<{tag}[^>]*>(.*?)</{tag}>", html_text, re.IGNORECASE | re.DOTALL
        ):
            stripper = _Stripper()
            stripper.feed(value)
            text = stripper.get_text().strip()
            if text:
                headings.append(text)
    links = []
    for href in re.findall(r"""href=["']([^"']+)["']""", html_text, re.IGNORECASE):
        value = urljoin(url, href)
        if value not in links:
            links.append(value)
        if len(links) >= 30:
            break
    return CompetitorArticle(
        url=url,
        title=title,
        canonical_url=canonicalize_url(url),
        clean_text=clean_text,
        headings=headings[:12],
        links=links,
        summary=clean_text[:1200].strip(),
        raw_html_hash=sha256_text(html_text),
        content_hash=sha256_text(clean_text),
    )


def extract_article(url: str, config: AppConfig, html_text: str | None = None) -> CompetitorArticle:
    payload = html_text if html_text is not None else fetch_html(url, config)
    article = (
        _extract_with_bs4(payload, url)
        if BeautifulSoup is not None
        else _extract_without_bs4(payload, url)
    )
    if html_text is not None or not _article_extraction_is_weak(article):
        return article
    try:
        browser_html = _browser_fallback_html(url, config)
        if browser_html:
            browser_article = (
                _extract_with_bs4(browser_html, url)
                if BeautifulSoup is not None
                else _extract_without_bs4(browser_html, url)
            )
            return browser_article
    except Exception as exc:
        import logging

        logging.debug(f"Secondary extraction fallback skipped: {exc}")
    return article
