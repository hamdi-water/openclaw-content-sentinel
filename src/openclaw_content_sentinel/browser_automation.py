from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast
from urllib.parse import urlparse

from .adapter_state import get_adapter_runtime, record_adapter_failure, record_adapter_success
from .config import AppConfig
from .selector_registry import load_selector_registry, selector_override_for
from .storage import RunStore
from .utils import dump_json, ensure_dir, load_json, now_utc, read_text, slugify, write_text
from .workflow import compose_url_for, prepare_publish, record_post_result

SNAPSHOT_REF_RE = re.compile(r"^\s*-\s*(.+?)\s*\[ref=([^\]]+)\](?:\s*\[[^\]]+\])*(?::\s*(.*))?$")
MEDIA_PATH_RE = re.compile(r"MEDIA:(.+)$")
OPEN_RESULT_ID_RE = re.compile(r"id:\s*([A-Z0-9]+)", re.IGNORECASE)
TAB_HEADER_RE = re.compile(r"^\s*(\d+)\.\s+(.*)$")
RETRYABLE_FAILURES = {"submit_failed", "confirmation_missing", "rate_limited"}
SHELL_SENSITIVE_TEXT_RE = re.compile(r"[|&<>^`]")


class JSSnippets:
    """Centralized collection of JavaScript snippets for browser automation."""

    INJECT_TEXT = (
        "() => {"
        "  var value = {payload};"
        "  var selectors = ['[contenteditable=\"true\"]','textarea','[role=\"textbox\"]','input[type=\"text\"]'];"  # noqa: E501
        "  var candidates = [];"
        "  var pushCandidate = function(el){"
        "    if (!el) return;"
        "    var meta = ((el.getAttribute('aria-label')||'')+' '+(el.getAttribute('placeholder')||'')+' '+(el.getAttribute('name')||'')).trim().toLowerCase();"  # noqa: E501
        "    if (meta.indexOf('search') !== -1) return;"
        "    if (el.getAttribute('role') === 'combobox') return;"
        "    if (el.disabled) return;"
        "    if (!(el.offsetParent !== null || el.getClientRects().length)) return;"
        "    var score = 0;"
        "    if (el.closest('[role=\"dialog\"]')) score += 200;"
        "    if (meta.indexOf('text editor for creating content') !== -1) score += 500;"
        "    if (meta.indexOf('what do you want to talk about') !== -1) score += 300;"
        "    if ((el.getAttribute('role')||'').toLowerCase() === 'textbox') score += 120;"
        "    if ((el.getAttribute('contenteditable')||'').toLowerCase() === 'true') score += 100;"
        "    if (el.tagName === 'TEXTAREA') score += 80;"
        "    if ((el.innerText||el.textContent||'').trim().length < 5) score += 15;"
        "    candidates.push({ el: el, score: score });"
        "  };"
        "  for (var i = 0; i < selectors.length; i += 1) {"
        "    var elements = document.querySelectorAll(selectors[i]);"
        "    for (var j = 0; j < elements.length; j += 1) pushCandidate(elements[j]);"
        "  }"
        "  candidates.sort(function(a,b){ return b.score - a.score; });"
        "  var target = candidates.length ? candidates[0].el : null;"
        "  if (!target) return false;"
        "  target.focus();"
        "  if ('value' in target) {"
        "    target.value = value;"
        "    target.dispatchEvent(new InputEvent('input', { bubbles: true, data: value, inputType: 'insertText' }));"  # noqa: E501
        "    target.dispatchEvent(new Event('change', { bubbles: true }));"
        "  } else {"
        "    target.textContent = value;"
        "    target.dispatchEvent(new InputEvent('input', { bubbles: true, data: value, inputType: 'insertText' }));"  # noqa: E501
        "    target.dispatchEvent(new KeyboardEvent('keyup', { bubbles: true, key: ' ' }));"
        "  }"
        "  return true;"
        "}"
    )

    CLICK_SUBMIT = (
        "() => {"
        "  var terms = {payload};"
        "  var elements = document.querySelectorAll('button, [role=\"button\"]');"
        "  var blocked = ['schedule', 'draft', 'add post'];"
        "  var findAndClick = function(exact) {"
        "    for (var i = 0; i < elements.length; i += 1) {"
        "      var el = elements[i];"
        "      var label = (el.innerText || el.textContent || el.getAttribute('aria-label') || '').trim().toLowerCase();"  # noqa: E501
        "      if (!label || el.disabled) continue;"
        "      if (blocked.some(function(t) { return label.indexOf(t) !== -1; })) continue;"
        "      for (var j = 0; j < terms.length; j += 1) {"
        "        if (exact ? label === terms[j] : label.indexOf(terms[j]) !== -1) {"
        "          el.click();"
        "          return true;"
        "        }"
        "      }"
        "    }"
        "    return false;"
        "  };"
        "  return findAndClick(true) || findAndClick(false);"
        "}"
    )

    FIND_FILE_INPUT = (
        "() => {"
        "  var selectors = {payload};"
        "  for (var i = 0; i < selectors.length; i += 1) {"
        "    var el = document.querySelector(selectors[i]);"
        "    if (el && el.tagName === 'INPUT' && el.type === 'file') return selectors[i];"
        "  }"
        "  var allInputs = document.querySelectorAll('input[type=file]');"
        "  for (var i = 0; i < allInputs.length; i += 1) {"
        "    var el = allInputs[i];"
        "    if (el.offsetParent !== null || el.getClientRects().length) return 'input[type=file]';"
        "  }"
        "  return '';"
        "}"
    )

    MEDIA_ATTACH_CONFIRMED = (
        "() => {"
        "  var text = (document.body.innerText || document.body.textContent || '').replace(/\\s+/g, ' ').trim().toLowerCase();"  # noqa: E501
        "  var labels = Array.from(document.querySelectorAll('button, [role=\"button\"]'))"
        "    .map(function(el) { return (el.innerText || el.textContent || el.getAttribute('aria-label') || '').replace(/\\s+/g, ' ').trim().toLowerCase(); })"  # noqa: E501
        "    .filter(Boolean);"
        "  var hasLabel = function(ts) { return labels.some(function(l) { return ts.some(function(t) { return l.indexOf(t) !== -1; }); }); };"  # noqa: E501
        "  var platform = {platform_payload};"
        "  if (platform === 'linkedin') {"
        "    if (document.querySelector('img[alt^=\"Preview of \"]')) return true;"
        "    return hasLabel(['alternative text', 'tag', 'duplicate', 'delete']) || text.indexOf('1 of 1') !== -1;"  # noqa: E501
        "  }"
        "  if (platform === 'facebook') {"
        "    return hasLabel(['supprimer la pièce jointe', 'modifier', 'remove media', 'remove photo', 'edit'])"  # noqa: E501
        '      || !!document.querySelector(\'img[src^="blob:"], img[src^="data:"]\');'
        "  }"
        "  if (platform === 'x') {"
        "    return hasLabel(['edit media', 'remove media']) || text.indexOf('media') !== -1;"
        "  }"
        "  return false;"
        "}"
    )

    IS_PUBLISH_SUCCESS = (
        "() => {"
        "  var platform = {platform_payload};"
        "  var terms = {terms_payload};"
        "  var normalize = function(v) { return (v || '').replace(/\\s+/g, ' ').trim().toLowerCase(); };"  # noqa: E501
        "  var text = normalize(document.body.innerText || document.body.textContent || '');"
        "  var hasLabel = function(ts) { return ts.some(function(t) { return text.indexOf(t.toLowerCase()) !== -1; }); };"  # noqa: E501
        "  if (hasLabel(terms)) return true;"
        "  if (platform === 'linkedin') {"
        "    if (document.querySelector('img[alt^=\"Preview of \"]')) return true;"
        "    return hasLabel(['alternative text', 'tag', 'duplicate', 'delete']) || text.indexOf('1 of 1') !== -1;"  # noqa: E501
        "  }"
        "  if (platform === 'facebook') {"
        "    return hasLabel(['supprimer la piece jointe', 'modifier', 'remove media', 'remove photo', 'edit']);"  # noqa: E501
        "  }"
        "  return false;"
        "}"
    )

    FIND_RECENT_POST = (
        "() => {"
        "  var normalize = function(v) { return (v || '').replace(/\\s+/g, ' ').trim().toLowerCase(); };"  # noqa: E501
        "  var needle = normalize({payload});"
        "  if (!needle) return JSON.stringify({ found: false, url: '' });"
        "  var platform = {platform_payload};"
        "  if (platform === 'x') {"
        "    var articles = document.querySelectorAll('article[role=\"article\"]');"
        "    for (var i = 0; i < articles.length; i += 1) {"
        "      var article = articles[i];"
        "      var text = normalize(article.innerText || article.textContent || '');"
        "      if (text.indexOf(needle) === -1) continue;"
        "      var anchors = Array.from(article.querySelectorAll('a[href*=\"/status/\"]'));"
        "      for (var j = 0; j < anchors.length; j += 1) {"
        "        if (anchors[j].href.indexOf('/status/') !== -1) return JSON.stringify({ found: true, url: anchors[j].href });"  # noqa: E501
        "      }"
        "    }"
        "  }"
        "  return JSON.stringify({ found: false, url: '' });"
        "}"
    )


@dataclass(frozen=True)
class SnapshotNode:
    ref: str
    text: str
    raw: str


@dataclass(frozen=True)
class PlatformSpec:
    platform: str
    compose_url: str
    composer_patterns: tuple[tuple[str, ...], ...]
    submit_patterns: tuple[tuple[str, ...], ...]
    media_patterns: tuple[tuple[str, ...], ...]
    success_texts: tuple[str, ...]
    file_input_selector: str = "input[type=file]"


@dataclass(frozen=True)
class BrowserTab:
    index: int
    title: str
    url: str
    target_id: str


class OpenClawBrowserError(RuntimeError):
    pass


class PublishAutomationError(RuntimeError):
    def __init__(self, failure_reason: str, message: str) -> None:
        super().__init__(message)
        self.failure_reason = failure_reason


def parse_snapshot(snapshot_text: str) -> list[SnapshotNode]:
    nodes: list[SnapshotNode] = []
    for line in snapshot_text.splitlines():
        match = SNAPSHOT_REF_RE.match(line)
        if not match:
            continue
        descriptor, ref, suffix = match.groups()
        text = " ".join(part for part in [descriptor, suffix or ""] if part).lower()
        text = text.replace('"', " ")
        text = re.sub(r"\s+", " ", text).strip()
        nodes.append(SnapshotNode(ref=ref, text=text, raw=line.rstrip()))
    return nodes


def find_node(snapshot_text: str, ref: str) -> SnapshotNode | None:
    if not ref:
        return None
    for node in parse_snapshot(snapshot_text):
        if node.ref == ref:
            return node
    return None


def find_ref(snapshot_text: str, patterns: tuple[tuple[str, ...], ...]) -> str:
    nodes = parse_snapshot(snapshot_text)
    for pattern in patterns:
        lowered = tuple(item.lower() for item in pattern)
        for node in nodes:
            if all(item in node.text for item in lowered):
                return node.ref
    return ""


def find_submit_ref(snapshot_text: str, spec: PlatformSpec) -> str:
    nodes = parse_snapshot(snapshot_text)
    terms = sorted(
        {
            token.lower()
            for pattern in spec.submit_patterns
            for token in pattern
            if token.lower() not in {"button", "link"}
        }
    )
    candidates: list[tuple[int, str]] = []
    for node in nodes:
        label = node.text.strip()
        if not label.startswith("button "):
            continue
        if any(blocked in label for blocked in ("schedule", "draft", "add post")):
            continue
        for term in terms:
            if term not in label:
                continue
            score = 0
            if label == f"button {term}":
                score += 500
            elif label.endswith(f" {term}"):
                score += 150
            else:
                score += 20
            candidates.append((score, node.ref))
            break
    candidates.sort(key=lambda item: item[0], reverse=True)
    return candidates[0][1] if candidates else ""


def first_text_input_ref(snapshot_text: str) -> str:
    nodes = parse_snapshot(snapshot_text)
    for node in nodes:
        if any(token in node.text for token in ("textbox", "textarea", "text editor", "compose")):
            return node.ref
    return ""


def find_composer_ref(snapshot_text: str, spec: PlatformSpec) -> str:
    lowered = snapshot_text.lower()
    dialog_open = (
        'dialog "créer une publication"' in lowered
        or 'dialog "create a post"' in lowered
        or 'dialog "start a post"' in lowered
    )
    if dialog_open:
        dialog_ref = find_ref(
            snapshot_text,
            (
                ("textbox", "post text"),
                ("textbox", "text editor"),
                ("textbox", "what do you want to talk about"),
                ("textbox",),
                ("textarea",),
            ),
        ) or first_text_input_ref(snapshot_text)
        if dialog_ref:
            return dialog_ref
    return find_ref(snapshot_text, spec.composer_patterns) or first_text_input_ref(snapshot_text)


def detect_failure_reason(snapshot_text: str, current_url: str = "") -> str:
    haystack = f"{current_url}\n{snapshot_text}".lower()
    if any(
        token in haystack
        for token in (
            "recaptcha",
            "suspicious activity",
            "security check",
            "security verification",
            "verify your identity",
            "verify you're human",
            "confirm you're not a bot",
            "enter the characters you see",
        )
    ):
        return "challenge_page"
    if any(
        token in haystack
        for token in (
            "sign in",
            "log in",
            "login",
            "session expired",
            "enter your password",
            "phone, email, or username",
        )
    ):
        return "not_logged_in"
    if any(
        token in haystack
        for token in ("try again later", "rate limit", "too many requests", "temporarily blocked")
    ):
        return "rate_limited"
    return ""


def text_requires_safe_entry(text: str) -> bool:
    payload = text or ""
    return bool(SHELL_SENSITIVE_TEXT_RE.search(payload) or "\n" in payload or "\r" in payload)


def parse_tabs_output(tabs_text: str) -> list[BrowserTab]:
    tabs: list[BrowserTab] = []
    current: dict[str, object] | None = None
    for line in tabs_text.splitlines():
        stripped = line.rstrip()
        header = TAB_HEADER_RE.match(stripped.strip())
        if header:
            if current and current.get("target_id"):
                tabs.append(
                    BrowserTab(
                        index=int(cast(int, current["index"])),
                        title=str(current["title"]),
                        url=str(current.get("url") or ""),
                        target_id=str(current["target_id"]),
                    )
                )
            current = {
                "index": int(header.group(1)),
                "title": header.group(2).strip(),
                "url": "",
                "target_id": "",
            }
            continue
        if not current:
            continue
        compact = stripped.strip()
        if compact.startswith("id:"):
            current["target_id"] = compact.split(":", 1)[1].strip()
            continue
        if compact and not current.get("url"):
            current["url"] = compact
    if current and current.get("target_id"):
        tabs.append(
            BrowserTab(
                index=int(cast(int, current["index"])),
                title=str(current["title"]),
                url=str(current.get("url") or ""),
                target_id=str(current["target_id"]),
            )
        )
    return tabs


def _normalized_host(url: str) -> str:
    try:
        return urlparse(url).netloc.lower().replace("www.", "")
    except Exception:
        return ""


def _tab_is_noise(tab: BrowserTab) -> bool:
    haystack = f"{tab.title}\n{tab.url}".lower()
    return any(
        token in haystack
        for token in (
            "chrome://",
            "chrome-untrusted://",
            "static_resources/webworker",
            "worker",
            "about:blank",
        )
    )


def _tab_matches_compose_url(tab: BrowserTab, compose_url: str) -> bool:
    compose_host = _normalized_host(compose_url)
    tab_host = _normalized_host(tab.url)
    if not compose_host or not tab_host or compose_host != tab_host:
        return False
    if "fbsbx.com" in tab_host:
        return False
    return True


def _select_reusable_tab(tabs: list[BrowserTab], spec: PlatformSpec) -> BrowserTab | None:
    candidates: list[tuple[int, BrowserTab]] = []
    compose_url = spec.compose_url.rstrip("/")
    for tab in tabs:
        if _tab_is_noise(tab) or not _tab_matches_compose_url(tab, compose_url):
            continue
        url = tab.url.rstrip("/")
        score = 0
        if url == compose_url:
            score += 300
        if spec.platform == "facebook" and url in {
            "https://facebook.com",
            "https://www.facebook.com",
        }:
            score += 220
        if spec.platform == "linkedin" and "sharebox" in url:
            score += 220
        if spec.platform == "x" and "/compose/post" in url:
            score += 220
        if len(url) <= len(compose_url) + 12:
            score += 20
        candidates.append((score, tab))
    if not candidates:
        return None
    candidates.sort(key=lambda item: (item[0], -item[1].index), reverse=True)
    return candidates[0][1]


def resolve_platform_spec(config: AppConfig, platform: str) -> PlatformSpec:
    specs = {
        "linkedin": PlatformSpec(
            platform="linkedin",
            compose_url=config.publish_url_linkedin,
            composer_patterns=(
                ("textbox", "post text"),
                ("textbox", "text editor"),
                ("start a post",),
                ("textbox",),
            ),
            submit_patterns=(
                ("button", "post"),
                ("button", "publish"),
                ("button", "share"),
            ),
            media_patterns=(
                ("button", "media"),
                ("button", "photo"),
                ("button", "image"),
            ),
            success_texts=(
                "post shared",
                "your post is now live",
                "posted successfully",
                "successfully posted",
                "post successful",
                "view post",
            ),
        ),
        "facebook": PlatformSpec(
            platform="facebook",
            compose_url=config.publish_url_facebook,
            composer_patterns=(
                ("button", "create a post"),
                ("button", "créer une publication"),
                ("button", "quoi de neuf"),
                ("button", "what's on your mind"),
                ("button", "what's up"),
                ("button", "write something"),
                ("region", "create a post"),
                ("region", "créer une publication"),
                ("textbox", "mind"),
                ("textbox", "quoi de neuf"),
                ("textbox", "write"),
                ("textarea",),
                ("textbox",),
            ),
            submit_patterns=(
                ("button", "post"),
                ("button", "publier"),
                ("button", "poster"),
                ("button", "publish"),
                ("button", "share"),
            ),
            media_patterns=(
                ("button", "photo/vidéo"),
                ("button", "photo/video"),
                ("button", "photo"),
                ("button", "image"),
                ("button", "media"),
            ),
            success_texts=(
                "your post is now published",
                "posted to your profile",
                "post published",
                "posted successfully",
                "publication",
                "publiée",
                "publié",
            ),
        ),
        "x": PlatformSpec(
            platform="x",
            compose_url=config.publish_url_x,
            composer_patterns=(
                ("textbox", "what's happening"),
                ("textbox", "post text"),
                ("textbox", "compose"),
                ("textbox",),
            ),
            submit_patterns=(
                ("button", "post"),
                ("button", "tweet"),
                ("button", "publish"),
            ),
            media_patterns=(
                ("button", "media"),
                ("button", "photo"),
                ("button", "image"),
            ),
            success_texts=(
                "your post was sent",
                "posted successfully",
                "post published",
                "successfully posted",
            ),
        ),
    }
    if platform not in specs:
        raise ValueError(f"Unsupported platform: {platform}")
    base = specs[platform]
    override = selector_override_for(config, platform)
    compose_url = str(override.get("compose_url") or base.compose_url).strip() or base.compose_url
    return PlatformSpec(
        platform=base.platform,
        compose_url=compose_url,
        composer_patterns=cast(
            tuple[tuple[str, ...], ...],
            override.get("composer_patterns") or base.composer_patterns,
        ),
        submit_patterns=cast(
            tuple[tuple[str, ...], ...],
            override.get("submit_patterns") or base.submit_patterns,
        ),
        media_patterns=cast(
            tuple[tuple[str, ...], ...],
            override.get("media_patterns") or base.media_patterns,
        ),
        success_texts=cast(tuple[str, ...], override.get("success_texts") or base.success_texts),
        file_input_selector=str(override.get("file_input_selector") or base.file_input_selector)
        .strip()
        or base.file_input_selector,
    )


def stage_upload_file(image_path: Path) -> Path:
    uploads_dir = ensure_dir(Path(tempfile.gettempdir()) / "openclaw" / "uploads")
    staged_name = f"{slugify(image_path.stem)}-{image_path.name}"
    staged_path = uploads_dir / staged_name
    shutil.copy2(image_path, staged_path)
    return staged_path


class OpenClawBrowserClient:
    def __init__(
        self, config: AppConfig, profile: str, simulation: bool = False, run_id: str = ""
    ) -> None:
        self.config = config
        self.profile = profile
        self.simulation = simulation
        self.run_id = run_id

    def _run(self, *args: str) -> str:
        executable = shutil.which(self.config.openclaw_bin) or self.config.openclaw_bin
        cmd = [
            executable,
            "browser",
            "--browser-profile",
            self.profile,
            "--timeout",
            str(self.config.browser_timeout_ms),
            *args,
        ]

        if self.simulation:
            log_msg = f"[{now_utc()}] SIMULATION: {' '.join(cmd)}"
            if self.run_id:
                store = RunStore(self.config)
                sim_log = store.run_dir(self.run_id) / "logs" / "simulation.log"
                ensure_dir(sim_log.parent)
                with open(sim_log, "a", encoding="utf-8") as f:
                    f.write(log_msg + "\n")

            # Mock responses for specific commands to keep the flow alive
            subcmd = args[0] if args else ""
            if subcmd == "open":
                return "id: MOCK_TARGET_123"
            if subcmd == "tabs":
                return "1. MOCK_TITLE - https://mock-url.com [id: MOCK_TARGET_123]"
            if subcmd == "snapshot":
                if "--out" in args:
                    out_idx = list(args).index("--out")
                    out_path = Path(args[out_idx + 1])
                    out_path.write_text(
                        "- MOCK_ELEMENT [ref=MOCK_REF_456] : Mock content", encoding="utf-8"
                    )
                return ""
            if subcmd == "evaluate":
                return "true"
            return ""

        result = subprocess.run(  # noqa: S603
            cmd,
            cwd=self.config.base_dir,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=max(30, int(self.config.browser_timeout_ms / 1000) + 15),
            check=False,
            shell=False,
        )
        stdout = (result.stdout or "").strip()
        stderr = (result.stderr or "").strip()
        if result.returncode != 0:
            raise OpenClawBrowserError(
                stderr or stdout or f"OpenClaw browser command failed: {' '.join(args)}"
            )
        return stdout

    def start(self) -> None:
        self._run("start")

    def open(self, url: str) -> str:
        output = self._run("open", url)
        match = OPEN_RESULT_ID_RE.search(output)
        if not match:
            raise OpenClawBrowserError(
                f"Unable to parse target id from browser open output: {output}"
            )
        return match.group(1)

    def tabs(self) -> list[BrowserTab]:
        return parse_tabs_output(self._run("tabs"))

    def focus(self, target_id: str) -> None:
        self._run("focus", target_id)

    def close(self, target_id: str) -> None:
        self._run("close", target_id)

    def navigate(self, url: str, target_id: str = "") -> None:
        if target_id:
            self.focus(target_id)
        self._run("navigate", url)

    def wait_time(self, time_ms: int, target_id: str) -> None:
        self._run("wait", "--time", str(time_ms), "--target-id", target_id)

    def wait_load(self, target_id: str, load: str = "domcontentloaded") -> None:
        self._run("wait", "--load", load, "--target-id", target_id)

    def wait_text(self, target_id: str, text: str) -> None:
        self._run("wait", "--text", text, "--target-id", target_id)

    def click(self, target_id: str, ref: str) -> None:
        self._run("click", ref, "--target-id", target_id)

    def type(self, target_id: str, ref: str, text: str) -> None:
        self._run("type", ref, text, "--target-id", target_id)

    def snapshot(self, target_id: str) -> str:
        with tempfile.NamedTemporaryFile(suffix=".txt", delete=False) as handle:
            snapshot_path = Path(handle.name)
        try:
            self._run(
                "snapshot", "--target-id", target_id, "--out", str(snapshot_path), "--limit", "800"
            )
            return snapshot_path.read_text(encoding="utf-8")
        finally:
            snapshot_path.unlink(missing_ok=True)

    def upload(
        self, target_id: str, file_path: Path, element_selector: str = "input[type=file]"
    ) -> None:
        self._run("upload", str(file_path), "--element", element_selector, "--target-id", target_id)

    def fill(self, target_id: str, ref: str, text: str) -> None:
        payload = [{"ref": ref, "value": text}]
        with tempfile.NamedTemporaryFile(
            suffix=".json", mode="w", encoding="utf-8", delete=False
        ) as handle:
            json.dump(payload, handle, ensure_ascii=True)
            fields_path = Path(handle.name)
        try:
            self._run("fill", "--fields-file", str(fields_path), "--target-id", target_id)
        finally:
            fields_path.unlink(missing_ok=True)

    def evaluate(self, fn_source: str, target_id: str = "") -> str:
        args = ["evaluate", "--fn", fn_source]
        if target_id:
            args.extend(["--target-id", target_id])
        return self._run(*args)

    def screenshot(self, target_id: str, destination: Path) -> Path:
        output = self._run("screenshot", target_id)
        match = MEDIA_PATH_RE.search(output)
        if not match:
            raise OpenClawBrowserError(f"Unable to parse screenshot path from output: {output}")
        source = Path(os.path.expanduser(match.group(1).strip()))
        ensure_dir(destination.parent)
        shutil.copy2(source, destination)
        return destination

    def current_url(self, target_id: str) -> str:
        for tab in self.tabs():
            if tab.target_id == target_id:
                return tab.url
        return ""


class AsyncBrowserPool:
    """Semaphore-guarded pool for concurrent browser automation."""

    def __init__(self, config: AppConfig, max_concurrent: int = 3) -> None:
        self.config = config
        self.semaphore = asyncio.Semaphore(max_concurrent)
        self.log = logging.getLogger(__name__)

    async def run_task(self, profile: str, func: Any, *args: Any, **kwargs: Any) -> Any:
        """Execute a browser function within the concurrency limit."""
        async with self.semaphore:
            self.log.info(f"Pool: Starting task for profile={profile}")
            # Ensure the function runs in a thread if it's sync
            if asyncio.iscoroutinefunction(func):
                return await func(*args, **kwargs)
            else:
                return await asyncio.to_thread(func, *args, **kwargs)


def _cleanup_platform_tabs(browser: OpenClawBrowserClient, spec: PlatformSpec) -> None:
    tabs = browser.tabs()
    duplicate_targets: list[str] = []
    matched_tabs = [
        tab
        for tab in tabs
        if _tab_matches_compose_url(tab, spec.compose_url) and not _tab_is_noise(tab)
    ]
    if spec.platform == "facebook":
        duplicate_targets.extend(
            tab.target_id for tab in tabs if "fbsbx.com/maw_proxy_page" in tab.url.lower()
        )
    if len(matched_tabs) > 1:
        for tab in matched_tabs[1:]:
            duplicate_targets.append(tab.target_id)
    seen: set[str] = set()
    for target_id in duplicate_targets:
        if not target_id or target_id in seen:
            continue
        seen.add(target_id)
        try:
            browser.close(target_id)
        except OpenClawBrowserError:
            continue


def _ensure_compose_target(browser: OpenClawBrowserClient, spec: PlatformSpec) -> str:
    _cleanup_platform_tabs(browser, spec)
    reusable = _select_reusable_tab(browser.tabs(), spec)
    if reusable:
        try:
            browser.focus(reusable.target_id)
            current_url = browser.current_url(reusable.target_id)
            current_host = _normalized_host(current_url)
            compose_host = _normalized_host(spec.compose_url)
            should_navigate = not current_url
            if spec.platform == "facebook":
                should_navigate = current_host != compose_host
            else:
                should_navigate = current_url.rstrip("/") != spec.compose_url.rstrip("/")
            if should_navigate:
                browser.navigate(spec.compose_url, target_id=reusable.target_id)
            return reusable.target_id
        except OpenClawBrowserError:
            pass
    return browser.open(spec.compose_url)


def inject_text_with_js(browser: OpenClawBrowserClient, target_id: str, text: str) -> bool:
    fn_source = JSSnippets.INJECT_TEXT.replace("{payload}", json.dumps(text))
    output = browser.evaluate(fn_source, target_id=target_id)
    return output.strip().strip('"').lower() == "true"


def click_submit_with_js(
    browser: OpenClawBrowserClient, target_id: str, spec: PlatformSpec
) -> bool:
    submit_terms = sorted(
        {
            token.lower()
            for pattern in spec.submit_patterns
            for token in pattern
            if token.lower() not in {"button", "link"}
        }
    )
    fn_source = JSSnippets.CLICK_SUBMIT.replace("{payload}", json.dumps(submit_terms))
    output = browser.evaluate(fn_source, target_id=target_id)
    return output.strip().strip('"').lower() == "true"


def click_button_with_terms(
    browser: OpenClawBrowserClient,
    target_id: str,
    terms: tuple[str, ...],
    blocked_terms: tuple[str, ...] = (),
) -> bool:
    payload = json.dumps(
        sorted({str(term or "").strip().lower() for term in terms if str(term or "").strip()})
    )
    blocked_payload = json.dumps(
        sorted(
            {str(term or "").strip().lower() for term in blocked_terms if str(term or "").strip()}
        )
    )
    fn_source = (
        "() => {"
        f"var terms = {payload};"
        f"var blocked = {blocked_payload};"
        "var elements = document.querySelectorAll('button, [role=\"button\"]');"
        "var pick = null;"
        "for (var i = 0; i < elements.length; i += 1) {"
        "var el = elements[i];"
        "if (!el || el.disabled) continue;"
        "var label = (el.innerText || el.textContent || el.getAttribute('aria-label') || '').replace(/\\s+/g, ' ').trim().toLowerCase();"  # noqa: E501
        "if (!label) continue;"
        "var blockedHit = blocked.some(function(token) { return label.indexOf(token) !== -1; });"
        "if (blockedHit) continue;"
        "for (var j = 0; j < terms.length; j += 1) {"
        "var term = terms[j];"
        "if (label === term) { el.click(); return true; }"
        "if (label.indexOf(term) !== -1) pick = el;"
        "}"
        "}"
        "if (pick) { pick.click(); return true; }"
        "return false;"
        "}"
    )
    output = browser.evaluate(fn_source, target_id=target_id)
    return output.strip().strip('"').lower() == "true"


def candidate_file_input_selectors(spec: PlatformSpec) -> tuple[str, ...]:
    selectors: list[str] = []
    if spec.platform == "x":
        selectors.extend(
            [
                "input[data-testid=fileInput]",
                "input[type=file][accept*='image']",
            ]
        )
    elif spec.platform == "facebook":
        selectors.extend(
            [
                "input[type=file][accept*='image']",
                "input[type=file][multiple]",
            ]
        )
    elif spec.platform == "linkedin":
        selectors.extend(
            [
                "input[type=file][accept*='image']",
                "input[type=file]",
            ]
        )
    selectors.append(spec.file_input_selector)
    deduped: list[str] = []
    for selector in selectors:
        normalized = str(selector or "").strip()
        if normalized and normalized not in deduped:
            deduped.append(normalized)
    return tuple(deduped)


def find_file_input_selector(
    browser: OpenClawBrowserClient, target_id: str, spec: PlatformSpec
) -> str:
    payload = json.dumps(candidate_file_input_selectors(spec))
    fn_source = (
        "() => {"
        f"var selectors = {payload};"
        "for (var i = 0; i < selectors.length; i += 1) {"
        "var selector = selectors[i];"
        "var el = document.querySelector(selector);"
        "if (!el || el.disabled) continue;"
        "return selector;"
        "}"
        "return '';"
        "}"
    )
    return browser.evaluate(fn_source, target_id=target_id).strip().strip('"')


def media_attachment_confirmed(
    browser: OpenClawBrowserClient, target_id: str, spec: PlatformSpec
) -> bool:
    fn_source = JSSnippets.MEDIA_ATTACH_CONFIRMED.replace(
        "{platform_payload}", json.dumps(spec.platform)
    )
    output = browser.evaluate(fn_source, target_id=target_id)
    return output.strip().strip('"').lower() == "true"


def _prepare_linkedin_media_upload(
    browser: OpenClawBrowserClient,
    target_id: str,
    spec: PlatformSpec,
    config: AppConfig,
    snapshot_text: str,
) -> str | None:
    if media_attachment_confirmed(browser, target_id, spec):
        if advance_linkedin_media_editor(browser, target_id):
            browser.wait_time(max(200, config.browser_modal_settle_ms), target_id)
            return "linkedin_media_reused"

    media_ref = find_ref(snapshot_text or browser.snapshot(target_id), spec.media_patterns)
    if media_ref:
        browser.click(target_id, media_ref)
        browser.wait_time(max(150, min(config.browser_modal_settle_ms, 350)), target_id)
        return None

    if media_attachment_confirmed(browser, target_id, spec):
        if advance_linkedin_media_editor(browser, target_id):
            browser.wait_time(max(200, config.browser_modal_settle_ms), target_id)
            return "linkedin_media_reused"
    return None


def _execute_media_upload_retry(
    browser: OpenClawBrowserClient,
    target_id: str,
    staged_file: Path,
    selector: str,
    config: AppConfig,
) -> None:
    last_error: Exception | None = None
    for _ in range(2):
        try:
            browser.upload(target_id, staged_file, element_selector=selector)
            return
        except OpenClawBrowserError as exc:
            last_error = exc
            browser.wait_time(max(250, config.browser_modal_settle_ms), target_id)
    if last_error:
        raise PublishAutomationError(
            "submit_failed", f"Image upload failed for {selector}: {last_error}"
        )


def attach_image_asset(
    browser: OpenClawBrowserClient,
    target_id: str,
    spec: PlatformSpec,
    image_path: Path,
    config: AppConfig,
    snapshot_text: str = "",
) -> list[str]:
    warnings: list[str] = []

    if spec.platform == "linkedin":
        res = _prepare_linkedin_media_upload(browser, target_id, spec, config, snapshot_text)
        if res == "linkedin_media_reused":
            return [res]

    staged_file = stage_upload_file(image_path)
    input_selector = find_file_input_selector(browser, target_id, spec)

    if not input_selector and spec.platform == "linkedin":
        input_selector = find_file_input_selector(browser, target_id, spec)
        if not input_selector:
            raise PublishAutomationError(
                "submit_failed",
                "LinkedIn media input did not appear after opening the media editor.",
            )

    if not input_selector:
        raise PublishAutomationError("submit_failed", f"Image input not found for {spec.platform}.")

    _execute_media_upload_retry(browser, target_id, staged_file, input_selector, config)

    browser.wait_time(max(350, config.browser_upload_settle_ms), target_id)
    if spec.platform == "linkedin":
        if not media_attachment_confirmed(browser, target_id, spec):
            browser.wait_time(max(250, config.browser_modal_settle_ms), target_id)
        if not media_attachment_confirmed(browser, target_id, spec):
            raise PublishAutomationError(
                "submit_failed", "LinkedIn image upload was not confirmed in the editor."
            )
        if advance_linkedin_media_editor(browser, target_id):
            warnings.append("linkedin_media_editor_advanced")
            browser.wait_time(max(200, config.browser_modal_settle_ms), target_id)

    if not media_attachment_confirmed(browser, target_id, spec):
        browser.wait_time(max(250, config.browser_input_settle_ms), target_id)
    if not media_attachment_confirmed(browser, target_id, spec):
        raise PublishAutomationError(
            "submit_failed", f"Image attachment was not confirmed for {spec.platform}."
        )

    return warnings


def advance_linkedin_media_editor(browser: OpenClawBrowserClient, target_id: str) -> bool:
    advanced = False
    for _ in range(3):
        snapshot = browser.snapshot(target_id).lower()
        if not any(token in snapshot for token in ("editor", "1 of 1", "crop", "alt")):
            break
        clicked = False
        for terms in (("next",), ("done", "apply", "save")):
            if click_button_with_terms(
                browser,
                target_id,
                terms,
                blocked_terms=("back", "cancel", "close", "discard", "draft", "schedule"),
            ):
                browser.wait_time(350, target_id)
                advanced = True
                clicked = True
                break
        if not clicked:
            break
    return advanced


def linkedin_editor_open(snapshot: str) -> bool:
    haystack = (snapshot or "").lower()
    return any(
        token in haystack
        for token in ('dialog "editor"', 'heading "editor"', "1 of 1", "alternative text")
    )


def recover_linkedin_compose_surface(
    browser: OpenClawBrowserClient,
    target_id: str,
    config: AppConfig,
    spec: PlatformSpec,
) -> str:
    snapshot = browser.snapshot(target_id)
    if not linkedin_editor_open(snapshot):
        return snapshot
    if advance_linkedin_media_editor(browser, target_id):
        browser.wait_time(max(200, config.browser_modal_settle_ms), target_id)
        snapshot = browser.snapshot(target_id)
        if not linkedin_editor_open(snapshot):
            return snapshot
    if click_button_with_terms(
        browser,
        target_id,
        ("dismiss", "close", "discard"),
        blocked_terms=("publish", "post", "done", "next"),
    ):
        browser.wait_time(max(200, config.browser_modal_settle_ms), target_id)
        snapshot = browser.snapshot(target_id)
        if not linkedin_editor_open(snapshot):
            return snapshot
    browser.navigate(spec.compose_url, target_id=target_id)
    browser.wait_load(target_id)
    browser.wait_time(config.browser_page_settle_ms, target_id)
    return browser.snapshot(target_id)


def _extract_x_handle_from_url(url: str) -> str:
    match = re.match(
        r"^https?://(?:www\.)?x\.com/([A-Za-z0-9_]{1,15})(?:/|$)",
        str(url or "").strip(),
        flags=re.IGNORECASE,
    )
    if not match:
        return ""
    candidate = match.group(1)
    if candidate.lower() in {
        "home",
        "explore",
        "notifications",
        "messages",
        "search",
        "compose",
        "i",
        "settings",
        "tos",
        "privacy",
    }:
        return ""
    return candidate


def extract_x_handle(browser: OpenClawBrowserClient, target_id: str) -> str:
    fn_source = (
        "() => {"
        "  var reserved = new Set(['home','explore','notifications','messages','search','compose','i','settings']);"  # noqa: E501
        "  var anchors = Array.from(document.querySelectorAll('a[href^=\"/\"]'));"
        "  for (var i = 0; i < anchors.length; i += 1) {"
        "    var href = anchors[i].getAttribute('href') || '';"
        "    var match = href.match(/^\\/([A-Za-z0-9_]{1,15})(?:$|[/?#])/);"
        "    if (!match) continue;"
        "    var handle = match[1];"
        "    if (handle && !reserved.has(handle.toLowerCase())) return handle;"
        "  }"
        "  var text = document.body.innerText || document.body.textContent || '';"
        "  var matches = text.match(/@[A-Za-z0-9_]{1,15}/g) || [];"
        "  for (var i = 0; i < matches.length; i += 1) {"
        "    var m = matches[i].slice(1);"
        "    if (m && m.toLowerCase() !== 'x') return m;"
        "  }"
        "  return '';"
        "}"
    )
    output = browser.evaluate(fn_source, target_id=target_id).strip().strip('"')
    if output:
        return output
    for tab in browser.tabs():
        handle = _extract_x_handle_from_url(tab.url)
        if handle:
            return handle
    return ""


def _verify_x_post_on_target(
    browser: OpenClawBrowserClient,
    target_id: str,
    snippet: str,
) -> tuple[bool, str]:
    fn_source = (
        "() => {"
        "  var normalize = function(v) { return (v || '').replace(/\\s+/g, ' ').trim().toLowerCase(); };"  # noqa: E501
        f"  var needle = normalize({json.dumps(snippet)});"
        "  if (!needle) return JSON.stringify({ found: false, url: '' });"
        "  var articles = document.querySelectorAll('article[role=\"article\"]');"
        "  for (var i = 0; i < articles.length; i += 1) {"
        "    var article = articles[i];"
        "    var text = normalize(article.innerText || article.textContent || '');"
        "    if (text.indexOf(needle) === -1) continue;"
        "    var anchors = Array.from(article.querySelectorAll('a[href*=\"/status/\"]'));"
        "    for (var j = 0; j < anchors.length; j += 1) {"
        "      if (anchors[j].href.indexOf('/status/') !== -1) return JSON.stringify({ found: true, url: anchors[j].href });"  # noqa: E501
        "    }"
        "  }"
        "  return JSON.stringify({ found: false, url: '' });"
        "}"
    )
    output = browser.evaluate(fn_source, target_id=target_id).strip().strip('"')
    try:
        result = json.loads(output)
    except json.JSONDecodeError:
        return False, ""
    return bool(result.get("found")), str(result.get("url") or "")


def _verify_recent_x_post_by_handle(
    browser: OpenClawBrowserClient,
    target_id: str,
    handle: str,
) -> tuple[bool, str]:
    payload = json.dumps((handle or "").lstrip("@"))
    fn_source = (
        "() => {"
        "var normalize = function(value) { return (value || '').replace(/\\s+/g, ' ').trim().toLowerCase(); };"  # noqa: E501
        f"var handle = normalize({payload});"
        "if (!handle) return JSON.stringify({ found: false, url: '' });"
        "var articles = document.querySelectorAll('article');"
        "for (var i = 0; i < articles.length; i += 1) {"
        "var article = articles[i];"
        "var statusLink = article.querySelector('a[href*=\"/status/\"]');"
        "if (!statusLink) continue;"
        "var href = statusLink.href || '';"
        "if (href.toLowerCase().indexOf('/' + handle + '/status/') === -1) continue;"
        "var timeNode = article.querySelector('time');"
        "var timeText = normalize(timeNode ? (timeNode.innerText || timeNode.textContent || '') : '');"  # noqa: E501
        "if (timeText && !/(now|s|m)$/.test(timeText) && !/^\\d+[sm]$/.test(timeText)) {"
        "return JSON.stringify({ found: true, url: href });"
        "}"
        "return JSON.stringify({ found: true, url: href });"
        "}"
        "return JSON.stringify({ found: false, url: '' });"
        "}"
    )
    output = browser.evaluate(fn_source, target_id=target_id).strip().strip('"')
    try:
        result = json.loads(output)
    except json.JSONDecodeError:
        return False, ""
    return bool(result.get("found")), str(result.get("url") or "")


def verify_x_post_publication(
    browser: OpenClawBrowserClient,
    target_id: str,
    draft_text: str,
) -> tuple[bool, str]:
    snippet = " ".join((draft_text or "").split())[:100]
    if not snippet:
        return False, ""
    found, url = _verify_x_post_on_target(browser, target_id, snippet)
    if found:
        return True, url
    handle = extract_x_handle(browser, target_id)
    if handle:
        found, url = _verify_recent_x_post_by_handle(browser, target_id, handle)
        if found:
            return True, url
    for tab in browser.tabs():
        if _tab_is_noise(tab) or _normalized_host(tab.url) != "x.com":
            continue
        try:
            browser.focus(tab.target_id)
            browser.wait_time(500, tab.target_id)
        except OpenClawBrowserError:
            continue
        found, url = _verify_x_post_on_target(browser, tab.target_id, snippet)
        if found:
            return True, url or tab.url
        if handle:
            found, url = _verify_recent_x_post_by_handle(browser, tab.target_id, handle)
            if found:
                return True, url or tab.url
    if not handle:
        handle = extract_x_handle(browser, target_id)
    if not handle:
        return False, ""
    profile_target = browser.open(f"https://x.com/{handle}")
    browser.wait_time(1200, profile_target)
    found, url = _verify_x_post_on_target(browser, profile_target, snippet)
    if found:
        return True, url or browser.current_url(profile_target)
    found, url = _verify_recent_x_post_by_handle(browser, profile_target, handle)
    if found:
        return True, url or browser.current_url(profile_target)
    return False, ""


def verify_facebook_post_publication(
    browser: OpenClawBrowserClient,
    target_id: str,
    draft_text: str,
) -> tuple[bool, str]:
    snippet = " ".join(draft_text.split())[:120]
    if not snippet:
        return False, ""
    payload = json.dumps(snippet)
    fn_source = (
        "() => {"
        "var normalize = function(value) { return (value || '').replace(/\\s+/g, ' ').trim(); };"
        f"var needle = normalize({payload});"
        "if (!needle) return JSON.stringify({ found: false, url: '' });"
        'var cards = document.querySelectorAll(\'[role="article"], [data-pagelet^="FeedUnit"], [data-pagelet^="ProfileTimeline"]\');'  # noqa: E501
        "for (var i = 0; i < cards.length; i += 1) {"
        "var card = cards[i];"
        "if (card.closest('[role=\"dialog\"]')) continue;"
        "var text = normalize(card.innerText || card.textContent || '');"
        "if (text && text.indexOf(needle) !== -1) return JSON.stringify({ found: true, url: window.location.href });"  # noqa: E501
        "}"
        'var hasOpenComposer = !!document.querySelector(\'[role="dialog"] [contenteditable="true"], [role="dialog"] textarea, [role="dialog"] [role="textbox"]\');'  # noqa: E501
        "var body = normalize(document.body.innerText || document.body.textContent || '');"
        "if (!hasOpenComposer && body.indexOf(needle) !== -1) return JSON.stringify({ found: true, url: window.location.href });"  # noqa: E501
        "return JSON.stringify({ found: false, url: '' });"
        "}"
    )
    output = browser.evaluate(fn_source, target_id=target_id).strip().strip('"')
    try:
        result = json.loads(output)
    except json.JSONDecodeError:
        return False, ""
    return bool(result.get("found")), str(result.get("url") or "")


def ensure_publish_ready(config: AppConfig, run_id: str, platform: str) -> dict[str, Any]:
    store = RunStore(config)
    run = store.load_run(run_id)
    result = (run.get("post_results") or {}).get(platform) or {}
    strategy = (run.get("analysis") or {}).get("publish_strategy") or {}
    platform_plan = (strategy.get("platforms") or {}).get(platform) or {}
    if result.get("status") == "ready_to_publish":
        draft_path = store.run_dir(run_id) / "drafts" / f"{platform}.md"
        image_path = store.run_dir(run_id) / "media" / "social_card.png"
        return {
            "run_id": run_id,
            "platform": platform,
            "status": run["status"],
            "approval_state": run["approval_state"],
            "attempt": result.get("attempts", 0),
            "browser_profile": result.get("browser_profile", ""),
            "draft_path": str(draft_path),
            "image_path": str(image_path) if image_path.exists() else "",
            "posting_checklist_path": str(store.run_dir(run_id) / "ops" / "posting_checklist.md"),
            "compose_url": compose_url_for(config, platform),
            "browser_health": platform_plan.get("browser_health") or {},
            "disclosure_text": platform_plan.get("disclosure_text", ""),
            "attribution_hint": platform_plan.get("attribution_hint", ""),
            "call_to_action": platform_plan.get("call_to_action", ""),
            "simulation": run.get("simulation") or {},
        }
    return prepare_publish(config, run_id, platform)


def _record_failure(
    config: AppConfig,
    run_id: str,
    platform: str,
    failure_reason: str,
    message: str,
    screenshots: list[str],
    *,
    spec: PlatformSpec | None = None,
    snapshot_text: str = "",
    current_url: str = "",
) -> dict[str, Any]:
    record_post_result(
        config,
        run_id,
        platform=platform,
        status="failed",
        note=message,
        failure_reason=failure_reason,
        screenshots=screenshots,
    )
    fixture_pack: dict[str, Any] = {}
    adapter_runtime: dict[str, Any] = {}
    if spec is not None:
        fixture_pack = _write_incident_fixture_pack(
            config,
            run_id,
            platform,
            spec,
            failure_reason,
            message,
            screenshots,
            snapshot_text=snapshot_text,
            current_url=current_url,
        )
        adapter_runtime = record_adapter_failure(
            config,
            platform,
            run_id,
            failure_reason,
            fixture_pack_path=str(fixture_pack.get("json_path") or ""),
            message=message,
        )
        _persist_post_result_metadata(
            config,
            run_id,
            platform,
            {
                "fixture_pack": str(fixture_pack.get("json_path") or ""),
                "fixture_pack_markdown": str(fixture_pack.get("markdown_path") or ""),
                "selector_registry_version": str(
                    fixture_pack.get("selector_registry_version") or ""
                ),
                "adapter_runtime": adapter_runtime,
            },
        )
    store = RunStore(config)
    run = store.load_run(run_id)
    return {
        "run_id": run_id,
        "platform": platform,
        "status": run["status"],
        "post_result": run["post_results"][platform],
    }


def _safe_browser_snapshot(browser: OpenClawBrowserClient, target_id: str) -> str:
    try:
        return browser.snapshot(target_id)
    except Exception:
        return ""


def _safe_browser_url(browser: OpenClawBrowserClient, target_id: str) -> str:
    try:
        return browser.current_url(target_id)
    except Exception:
        return ""


def _persist_post_result_metadata(
    config: AppConfig, run_id: str, platform: str, metadata: dict[str, Any]
) -> dict[str, Any]:
    store = RunStore(config)
    run = store.load_run(run_id)
    result = cast(dict[str, Any], (run.get("post_results") or {}).get(platform) or {})
    result.update(metadata)
    result["updated_at"] = now_utc()
    run.setdefault("post_results", {})[platform] = result
    run["updated_at"] = now_utc()
    store.save_run(run)
    return run


def _write_incident_fixture_pack(
    config: AppConfig,
    run_id: str,
    platform: str,
    spec: PlatformSpec,
    failure_reason: str,
    message: str,
    screenshots: list[str],
    *,
    snapshot_text: str = "",
    current_url: str = "",
) -> dict[str, Any]:
    timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    incident_id = f"{platform}-{timestamp}"
    incident_dir = ensure_dir(config.data_dir / "adapters" / "incidents" / run_id / incident_id)
    snapshot_path_text = ""
    if snapshot_text.strip():
        snapshot_path = incident_dir / "snapshot.txt"
        write_text(snapshot_path, snapshot_text)
        snapshot_path_text = str(snapshot_path)
    health = load_json(config.data_dir / "browser-health" / f"{platform}.json", default={}) or {}
    selector_registry = load_selector_registry(config)
    payload = {
        "incident_id": incident_id,
        "created_at": now_utc(),
        "run_id": run_id,
        "platform": platform,
        "failure_reason": failure_reason,
        "message": message,
        "current_url": current_url,
        "screenshots": list(screenshots),
        "snapshot_path": snapshot_path_text,
        "selector_registry_version": selector_registry.get("registry_version", ""),
        "selector_registry_path": selector_registry.get("path", ""),
        "publish_adapter_version": config.publish_adapter_version,
        "browser_health": health if isinstance(health, dict) else {},
        "adapter_contract": {
            "compose_url": spec.compose_url,
            "composer_patterns": [list(item) for item in spec.composer_patterns],
            "submit_patterns": [list(item) for item in spec.submit_patterns],
            "media_patterns": [list(item) for item in spec.media_patterns],
            "success_texts": list(spec.success_texts),
            "file_input_selector": spec.file_input_selector,
        },
        "replay_hints": {
            "verification_replay": (
                f"python -m openclaw_content_sentinel.cli browser-publish --run-id {run_id} "
                f"--platform {platform} --no-submit"
            ),
            "submit_replay": (
                f"python -m openclaw_content_sentinel.cli browser-publish --run-id {run_id} "
                f"--platform {platform}"
            ),
            "graph_replay": (
                f"python -m openclaw_content_sentinel.cli replay --run-id {run_id} "
                "--node approval_gate"
            ),
        },
    }
    json_path = incident_dir / "fixture-pack.json"
    md_path = incident_dir / "fixture-pack.md"
    dump_json(json_path, payload)
    lines = [
        "# Incident Fixture Pack",
        "",
        f"- Incident ID: {incident_id}",
        f"- Run ID: {run_id}",
        f"- Platform: {platform}",
        f"- Failure reason: {failure_reason}",
        f"- Message: {message}",
        f"- Current URL: {current_url or 'n/a'}",
        f"- Publish adapter version: {config.publish_adapter_version}",
        f"- Selector registry version: {selector_registry.get('registry_version', '')}",
        f"- Snapshot: {snapshot_path_text or 'n/a'}",
        "",
        "## Replay Hints",
        "",
        f"- Verification replay: `{payload['replay_hints']['verification_replay']}`",
        f"- Submit replay: `{payload['replay_hints']['submit_replay']}`",
        f"- Graph replay: `{payload['replay_hints']['graph_replay']}`",
    ]
    write_text(md_path, "\n".join(lines))
    return {
        "incident_id": incident_id,
        "json_path": str(json_path),
        "markdown_path": str(md_path),
        "selector_registry_version": str(selector_registry.get("registry_version") or ""),
    }


def _load_cached_browser_health(config: AppConfig, platform: str) -> dict[str, Any]:
    path = config.data_dir / "browser-health" / f"{platform}.json"
    if not path.exists():
        return {}
    payload = load_json(path, default={})
    if not isinstance(payload, dict):
        return {}
    checked_at = str(payload.get("checked_at") or "").strip()
    if not checked_at:
        return {}
    try:
        checked_at_dt = datetime.fromisoformat(checked_at.replace("Z", "+00:00"))
    except ValueError:
        return {}
    age = (datetime.now(UTC) - checked_at_dt).total_seconds()
    if age > max(0, config.browser_health_cache_seconds):
        return {}
    return payload


def check_browser_profile_health(
    config: AppConfig, platform: str, force_refresh: bool = False
) -> dict[str, Any]:
    if not force_refresh:
        cached = _load_cached_browser_health(config, platform)
        if cached:
            return cached
    spec = resolve_platform_spec(config, platform)
    profile = {
        "linkedin": config.browser_profile_linkedin,
        "facebook": config.browser_profile_facebook,
        "x": config.browser_profile_x,
    }[platform]
    browser = OpenClawBrowserClient(config, profile)
    browser.start()
    target_id = _ensure_compose_target(browser, spec)
    screenshot_path = config.data_dir / "browser-health" / f"{platform}.png"

    try:
        browser.wait_load(target_id)
        browser.wait_time(config.browser_page_settle_ms, target_id)
        snapshot = browser.snapshot(target_id)
        current_url = browser.current_url(target_id)
        failure_reason = detect_failure_reason(snapshot, current_url)
        composer_ref = find_composer_ref(snapshot, spec)
        browser.screenshot(target_id, screenshot_path)

        if failure_reason:
            result = {
                "platform": platform,
                "profile": profile,
                "status": "attention",
                "reason": failure_reason,
                "compose_url": spec.compose_url,
                "current_url": current_url,
                "composer_detected": bool(composer_ref),
                "screenshot": str(screenshot_path),
            }
            result["checked_at"] = datetime.now(UTC).isoformat()
            dump_json(config.data_dir / "browser-health" / f"{platform}.json", result)
            return result

        result = {
            "platform": platform,
            "profile": profile,
            "status": "ready" if composer_ref else "attention",
            "reason": "" if composer_ref else "composer_missing",
            "compose_url": spec.compose_url,
            "current_url": current_url,
            "composer_detected": bool(composer_ref),
            "screenshot": str(screenshot_path),
            "checked_at": datetime.now(UTC).isoformat(),
        }
        dump_json(config.data_dir / "browser-health" / f"{platform}.json", result)
        return result
    except Exception as exc:
        try:
            browser.screenshot(target_id, screenshot_path)
        except Exception as screenshot_exc:
            # Screenshot failure is non-fatal for JSON output
            logging.error(f"Screenshot failed during health check: {screenshot_exc}")
        result = {
            "platform": platform,
            "profile": profile,
            "status": "error",
            "reason": str(exc),
            "compose_url": spec.compose_url,
            "current_url": "",
            "composer_detected": False,
            "screenshot": str(screenshot_path) if screenshot_path.exists() else "",
            "checked_at": datetime.now(UTC).isoformat(),
        }
        dump_json(config.data_dir / "browser-health" / f"{platform}.json", result)
        return result


def check_all_browser_profiles(config: AppConfig, force_refresh: bool = False) -> dict[str, Any]:
    results = [
        check_browser_profile_health(config, platform, force_refresh=force_refresh)
        for platform in ("linkedin", "facebook", "x")
    ]
    ready = sum(1 for item in results if item["status"] == "ready")
    payload = {
        "status": "ok" if ready == len(results) else "attention",
        "ready_profiles": ready,
        "total_profiles": len(results),
        "results": results,
    }
    dump_json(config.data_dir / "browser-health" / "summary.json", payload)
    return payload


def _prepare_publish_session(
    browser: OpenClawBrowserClient, target_id: str, spec: PlatformSpec, config: AppConfig
) -> str:
    browser.wait_load(target_id)
    browser.wait_time(config.browser_page_settle_ms, target_id)
    snapshot = browser.snapshot(target_id)
    if spec.platform == "linkedin":
        snapshot = recover_linkedin_compose_surface(browser, target_id, config, spec)
    current_url = browser.current_url(target_id)
    failure_reason = detect_failure_reason(snapshot, current_url)
    if failure_reason:
        raise PublishAutomationError(
            failure_reason, f"Publishing blocked for {spec.platform}: {failure_reason}"
        )
    return snapshot


def _inject_publish_content(
    browser: OpenClawBrowserClient,
    target_id: str,
    spec: PlatformSpec,
    config: AppConfig,
    draft_text: str,
    snapshot: str,
) -> str:
    composer_ref = find_composer_ref(snapshot, spec)
    if not composer_ref:
        msg = f"Composer input not found for {spec.platform}."
        raise PublishAutomationError("composer_missing", msg)

    composer_node = find_node(snapshot, composer_ref)
    composer_text = (composer_node.text or "").lower() if composer_node else ""
    if composer_node and any(
        m in composer_text
        for m in (
            "start a post",
            "create a post",
            "créer une publication",
            "what's on your mind",
            "quoi de neuf",
        )
    ):
        browser.click(target_id, composer_ref)
        browser.wait_time(config.browser_modal_settle_ms, target_id)
        snapshot = browser.snapshot(target_id)
        if spec.platform == "linkedin":
            snapshot = recover_linkedin_compose_surface(browser, target_id, config, spec)
        composer_ref = find_composer_ref(snapshot, spec)

    if not composer_ref:
        msg = f"Composer modal did not expose a text input for {spec.platform}."
        raise PublishAutomationError("composer_missing", msg)

    try:
        browser.click(target_id, composer_ref)
    except OpenClawBrowserError:
        pass  # Non-critical

    safe_entry = text_requires_safe_entry(draft_text)
    method = browser.fill if (spec.platform != "x" or safe_entry) else browser.type
    try:
        method(target_id, composer_ref, draft_text)
    except OpenClawBrowserError:
        if not inject_text_with_js(browser, target_id, draft_text):
            msg = f"Unable to enter draft text for {spec.platform}."
            raise PublishAutomationError("composer_missing", msg) from None

    browser.wait_time(config.browser_input_settle_ms, target_id)
    return browser.snapshot(target_id)


def _submit_publish_form(
    browser: OpenClawBrowserClient,
    target_id: str,
    spec: PlatformSpec,
    config: AppConfig,
    snapshot: str,
) -> None:
    submit_ref = find_submit_ref(snapshot, spec)
    if submit_ref:
        try:
            browser.click(target_id, submit_ref)
        except OpenClawBrowserError:
            if not click_submit_with_js(browser, target_id, spec):
                msg = f"Submit button not found for {spec.platform}."
                raise PublishAutomationError("submit_failed", msg) from None
    elif not click_submit_with_js(browser, target_id, spec):
        msg = f"Submit button not found for {spec.platform}."
        raise PublishAutomationError("submit_failed", msg)

    to = (
        config.browser_submit_wait_ms_facebook
        if spec.platform == "facebook"
        else config.browser_submit_wait_ms
    )
    browser.wait_time(to, target_id)


def _finalize_publish_result(
    browser: OpenClawBrowserClient, target_id: str, spec: PlatformSpec, draft_text: str
) -> tuple[bool, str]:
    current_url = browser.current_url(target_id)
    final_snapshot = browser.snapshot(target_id)
    failure_reason = detect_failure_reason(final_snapshot, current_url)
    if failure_reason:
        msg = f"Publishing blocked after submit: {failure_reason}"
        raise PublishAutomationError(failure_reason, msg)

    success = any(token in final_snapshot.lower() for token in spec.success_texts)
    if spec.platform == "x":
        if "/status/" in current_url:
            success = True
        elif not success:
            v_ok, v_url = verify_x_post_publication(browser, target_id, draft_text)
            if v_ok:
                success, current_url = True, v_url
    elif spec.platform == "facebook" and not success:
        v_ok, v_url = verify_facebook_post_publication(browser, target_id, draft_text)
        if v_ok:
            success, current_url = True, v_url
    elif not success and current_url and current_url.rstrip("/") != spec.compose_url.rstrip("/"):
        success = True

    return success, current_url


def _publish_via_browser_once(
    config: AppConfig, run_id: str, platform: str, submit: bool = True
) -> dict[str, Any]:
    store = RunStore(config)
    spec = resolve_platform_spec(config, platform)
    if submit:
        adapter_runtime = get_adapter_runtime(config, platform)
        freeze = cast(dict[str, Any], adapter_runtime.get("freeze") or {})
        if freeze.get("active"):
            message = (
                f"Publishing is frozen for {platform} until "
                f"{freeze.get('until') or 'operator intervention'}."
            )
            record_post_result(
                config,
                run_id,
                platform=platform,
                status="failed",
                note=message,
                failure_reason="adapter_frozen",
                screenshots=[],
            )
            _persist_post_result_metadata(
                config,
                run_id,
                platform,
                {"adapter_runtime": adapter_runtime},
            )
            run = store.load_run(run_id)
            return {
                "run_id": run_id,
                "platform": platform,
                "status": run["status"],
                "post_result": run["post_results"][platform],
            }
    prepared = ensure_publish_ready(config, run_id, platform)
    draft_text = read_text(Path(prepared["draft_path"])).strip()

    is_sim = bool((prepared.get("simulation") or {}).get("enabled", False))
    browser = OpenClawBrowserClient(
        config, prepared["browser_profile"], simulation=is_sim, run_id=run_id
    )
    browser.start()
    target_id = _ensure_compose_target(browser, spec)
    screenshots: list[str] = []
    warnings: list[str] = []

    try:
        snapshot = _prepare_publish_session(browser, target_id, spec, config)
        snapshot = _inject_publish_content(browser, target_id, spec, config, draft_text, snapshot)

        image_path = Path(prepared["image_path"]) if prepared.get("image_path") else None
        if image_path and image_path.exists():
            res_warnings = attach_image_asset(
                browser, target_id, spec, image_path, config, snapshot_text=snapshot
            )
            warnings.extend(res_warnings)
            snapshot = browser.snapshot(target_id)

        pre_submit_path = store.run_dir(run_id) / "media" / f"{platform}-pre-submit.png"
        screenshots.append(str(browser.screenshot(target_id, pre_submit_path)))

        if not submit:
            n = f"Preparation completed.{' Warnings: ' + '; '.join(warnings) if warnings else ''}"
            record_post_result(
                config,
                run_id,
                platform=platform,
                status="ready_to_publish",
                note=n,
                screenshots=screenshots,
            )
            latest = store.load_run(run_id)
            return {
                "run_id": run_id,
                "platform": platform,
                "status": latest["status"],
                "post_result": latest["post_results"][platform],
            }

        _submit_publish_form(browser, target_id, spec, config, snapshot)
        post_submit_path = store.run_dir(run_id) / "media" / f"{platform}-post-submit.png"
        screenshots.append(str(browser.screenshot(target_id, post_submit_path)))

        success, final_url = _finalize_publish_result(browser, target_id, spec, draft_text)
        if not success:
            msg = f"Publish confirmation not detected for {platform}."
            raise PublishAutomationError("confirmation_missing", msg)

        record_post_result(
            config,
            run_id,
            platform=platform,
            status="posted",
            url=final_url,
            note="; ".join(warnings),
            screenshots=screenshots,
        )
        adapter_runtime = record_adapter_success(config, platform, run_id, final_url=final_url)
        _persist_post_result_metadata(
            config,
            run_id,
            platform,
            {"adapter_runtime": adapter_runtime},
        )
        latest = store.load_run(run_id)
        return {
            "run_id": run_id,
            "platform": platform,
            "status": latest["status"],
            "post_result": latest["post_results"][platform],
        }

    except PublishAutomationError as exc:
        snapshot_text = _safe_browser_snapshot(browser, target_id)
        current_url = _safe_browser_url(browser, target_id)
        failure_path = store.run_dir(run_id) / "media" / f"{platform}-failure.png"
        try:
            screenshots.append(str(browser.screenshot(target_id, failure_path)))
        except Exception as screenshot_exc:
            # Ignored during failure recovery
            logging.error(f"Failure screenshot failed: {screenshot_exc}")
        return _record_failure(
            config,
            run_id,
            platform,
            exc.failure_reason,
            str(exc),
            screenshots,
            spec=spec,
            snapshot_text=snapshot_text,
            current_url=current_url,
        )
    except Exception as exc:
        snapshot_text = _safe_browser_snapshot(browser, target_id)
        current_url = _safe_browser_url(browser, target_id)
        failure_path = store.run_dir(run_id) / "media" / f"{platform}-failure.png"
        try:
            screenshots.append(str(browser.screenshot(target_id, failure_path)))
        except Exception as screenshot_exc:
            # Screenshot failure ignored during secondary recovery
            logging.error(f"Secondary recovery screenshot failed: {screenshot_exc}")
        return _record_failure(
            config,
            run_id,
            platform,
            "submit_failed",
            str(exc),
            screenshots,
            spec=spec,
            snapshot_text=snapshot_text,
            current_url=current_url,
        )


def publish_via_browser(
    config: AppConfig,
    run_id: str,
    platform: str,
    submit: bool = True,
    retries: int | None = None,
) -> dict[str, Any]:
    attempts_remaining = max(0, config.browser_publish_retries if retries is None else retries)
    latest_result = {}
    attempt_index = 0

    while True:
        attempt_index += 1
        latest_result = _publish_via_browser_once(config, run_id, platform, submit=submit)
        post_result = latest_result.get("post_result") or {}
        if post_result.get("status") != "failed":
            latest_result["retry_attempts_used"] = attempt_index - 1
            return latest_result

        failure_reason = post_result.get("failure_reason") or ""
        if not submit or attempts_remaining <= 0 or failure_reason not in RETRYABLE_FAILURES:
            latest_result["retry_attempts_used"] = attempt_index - 1
            return latest_result

        attempts_remaining -= 1
        note = post_result.get("note") or ""
        suffix = f" Retry scheduled after transient failure: {failure_reason}."
        if suffix.strip() not in note:
            record_post_result(
                config,
                run_id,
                platform=platform,
                status="failed",
                note=f"{note}{suffix}".strip(),
                screenshots=post_result.get("screenshots") or [],
                failure_reason=failure_reason,
            )
        if config.browser_retry_wait_ms > 0:
            import time

            time.sleep(config.browser_retry_wait_ms / 1000)


def publish_all_via_browser(
    config: AppConfig,
    run_id: str,
    submit: bool = True,
    continue_on_error: bool = True,
    retries: int | None = None,
) -> dict[str, Any]:
    store = RunStore(config)
    run = store.load_run(run_id)
    results = []
    for platform in run.get("platform_targets") or []:
        result = publish_via_browser(config, run_id, platform, submit=submit, retries=retries)
        latest = store.load_run(run_id)
        post_result = result["post_result"]
        results.append(
            {
                "platform": platform,
                "status": post_result["status"],
                "failure_reason": post_result["failure_reason"],
                "url": post_result["url"],
                "screenshots": post_result["screenshots"],
                "fixture_pack": post_result.get("fixture_pack", ""),
                "attempts": post_result["attempts"],
                "retry_attempts_used": result.get("retry_attempts_used", 0),
                "run_status": latest["status"],
            }
        )
        if latest["post_results"][platform]["status"] == "failed" and not continue_on_error:
            break
    latest = store.load_run(run_id)
    return {
        "run_id": run_id,
        "submit": submit,
        "status": latest["status"],
        "results": results,
    }


async def publish_all_via_browser_async(
    config: AppConfig,
    run_id: str,
    submit: bool = True,
    retries: int | None = None,
) -> dict[str, Any]:
    """Parallelized version of publish_all_via_browser using AsyncBrowserPool."""
    store = RunStore(config)
    run = store.load_run(run_id)
    platforms = run.get("platform_targets") or []
    if not platforms:
        return {"run_id": run_id, "status": run["status"], "results": []}

    pool = AsyncBrowserPool(config)

    async def _publish(platform: str) -> dict[str, Any]:
        return await pool.run_task(
            platform, publish_via_browser, config, run_id, platform, submit=submit, retries=retries
        )

    results = await asyncio.gather(*[_publish(p) for p in platforms])

    latest = store.load_run(run_id)
    return {
        "run_id": run_id,
        "submit": submit,
        "status": latest["status"],
        "results": results,
    }


def publish_variation(
    config: AppConfig,
    run_id: str,
    platform: str,
    variation_text: str,
    submit: bool = True,
) -> dict[str, Any]:
    """Ref §35.1: Publish a specific content variation to a platform."""
    # Temporarily override the draft file to use the variation
    store = RunStore(config)
    draft_dir = store.run_dir(run_id) / "drafts"
    draft_dir.mkdir(parents=True, exist_ok=True)
    draft_path = draft_dir / f"{platform}.md"
    original_text = ""
    if draft_path.exists():
        original_text = draft_path.read_text(encoding="utf-8")

    try:
        draft_path.write_text(variation_text, encoding="utf-8")
        result = publish_via_browser(config, run_id, platform, submit=submit)
        return result
    finally:
        if original_text:
            draft_path.write_text(original_text, encoding="utf-8")
