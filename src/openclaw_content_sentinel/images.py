from __future__ import annotations

import hashlib
import json
import random
import textwrap
import time
from dataclasses import replace
from pathlib import Path
from typing import Any, cast
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from PIL import Image, ImageDraw, ImageFilter, ImageFont

from .config import AppConfig
from .identity import file_sha256
from .utils import ensure_dir, load_json

CANVAS_SIZE = (1200, 630)
BACKGROUND_TOP = (11, 33, 58)
BACKGROUND_BOTTOM = (36, 74, 120)
TEXT_MAIN = (245, 248, 252)
TEXT_MUTED = (196, 209, 224)
CARD = (255, 255, 255, 26)


def _load_font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    candidates = [
        "DejaVuSans-Bold.ttf" if bold else "DejaVuSans.ttf",
        "arialbd.ttf" if bold else "arial.ttf",
    ]
    for candidate in candidates:
        try:
            return ImageFont.truetype(candidate, size=size)
        except OSError:
            continue
    return ImageFont.load_default()


def _wrap(text: str, width: int) -> list[str]:
    payload = " ".join(text.split())
    if not payload:
        return []
    return textwrap.wrap(payload, width=width)


def _seed_for_run(config: AppConfig, run: dict[str, Any]) -> int:
    payload = (
        f"{config.image_seed}|{run.get('run_id', '')}|"
        f"{run.get('prompt', '')}|{','.join(run.get('keywords') or [])}"
    )
    digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()
    return int(digest[:12], 16)


def _palette_for_run(config: AppConfig, run: dict[str, Any]) -> list[tuple[int, int, int]]:
    layers = list(cast(list[dict[str, Any]], (run.get("layers") or [])))
    accents = [
        (255, 167, 38),
        (110, 231, 183),
        (96, 165, 250),
        (244, 114, 182),
        (251, 191, 36),
        (129, 140, 248),
    ]
    if not layers:
        return accents[:4]
    _palette: list[tuple[int, int, int]] = [
        (int(digest[i : i + 2], 16), int(digest[i + 2 : i + 4], 16), int(digest[i + 4 : i + 6], 16))
        for layer in layers[:4]
        for digest in [hashlib.sha256(str(layer).encode("utf-8")).hexdigest()]
        for i in [0]
    ]
    return _palette or accents[:4]


def _draw_gradient(draw: ImageDraw.ImageDraw, width: int, height: int) -> None:
    for y in range(height):
        mix = y / max(height - 1, 1)
        color = tuple(
            int(BACKGROUND_TOP[index] * (1 - mix) + BACKGROUND_BOTTOM[index] * mix)
            for index in range(3)
        )
        draw.line((0, y, width, y), fill=color)


def _draw_procedural_art(
    image: Image.Image, rng: random.Random, palette: list[tuple[int, int, int]]
) -> None:
    width, height = image.size
    overlay = Image.new("RGBA", image.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)

    for index in range(18):
        color = palette[index % len(palette)] + (70 + (index % 4) * 18,)
        x0 = rng.randint(-120, width - 80)
        y0 = rng.randint(-80, height - 60)
        x1 = x0 + rng.randint(140, 460)
        y1 = y0 + rng.randint(140, 420)
        if index % 3 == 0:
            draw.ellipse((x0, y0, x1, y1), fill=color)
        elif index % 3 == 1:
            draw.rounded_rectangle((x0, y0, x1, y1), radius=rng.randint(24, 80), fill=color)
        else:
            draw.polygon(
                [
                    (x0, y0 + rng.randint(10, 90)),
                    (x0 + rng.randint(90, 160), y0),
                    (x1, y0 + rng.randint(50, 120)),
                    (x0 + rng.randint(40, 120), y1),
                ],
                fill=color,
            )

    for _ in range(7):
        base_y = rng.randint(40, height - 40)
        points = []
        for x in range(-40, width + 80, 90):
            points.append((x, base_y + rng.randint(-70, 70)))
        draw.line(
            points,
            fill=palette[rng.randint(0, len(palette) - 1)] + (120,),
            width=rng.randint(4, 12),
        )

    blurred = overlay.filter(ImageFilter.GaussianBlur(radius=18))
    image.alpha_composite(blurred)


def _canvas_size(config: AppConfig) -> tuple[int, int]:
    return (max(512, int(config.image_width)), max(512, int(config.image_height)))


def _image_prompt_for_run(run: dict[str, Any]) -> str:
    article = run.get("article") or {}
    keywords = ", ".join((run.get("keywords") or [])[:5])
    trend = ""
    if run.get("trend_signals"):
        trend = (run["trend_signals"][0] or {}).get("title", "")
    parts = [
        "Create an original editorial social visual for OpenClaw Content Sentinel.",
        f"Topic: {article.get('title') or run.get('prompt') or 'AI operations'}",
        f"Angle: {(run.get('analysis') or {}).get('original_angle', '').strip()}",
        f"Keywords: {keywords}",
        f"Trend context: {trend}",
        "Style: clean crystalline interface, high contrast, modern editorial, "
        "no watermarks, no logos, no text artifacts.",
        "Composition: 16:9 landscape social card, abstract shapes, subtle gradients, "
        "premium B2B technology aesthetic, clear headline hierarchy, insight strip, "
        "evidence chips, and CTA footer.",
    ]
    return " ".join(part for part in parts if part and not part.endswith(": "))


def _truncate_text(value: str, limit: int) -> str:
    payload = " ".join((value or "").split())
    if len(payload) <= limit:
        return payload
    return payload[: max(0, limit - 1)].rstrip() + "..."


def _visual_brief(run: dict[str, Any]) -> dict[str, object]:
    article = run.get("article") or {}
    daily_input = run.get("daily_input") or {}
    analysis = run.get("analysis") or {}
    trend_signals = run.get("trend_signals") or []
    title = (
        str(daily_input.get("topic") or "").strip()
        or str(article.get("title") or "").strip()
        or str(run.get("prompt") or "OpenClaw Content Sentinel").strip()
    )
    insight = (
        str(daily_input.get("business_angle") or "").strip()
        or _truncate_text(str(analysis.get("original_angle") or "").strip(), 180)
        or "Operational reliability turns content generation into a controlled system."
    )
    cta = str(daily_input.get("key_call_to_action") or "").strip()
    chips = [
        item.strip()
        for item in (daily_input.get("mandatory_references") or [])
        if str(item).strip()
    ]
    if not chips:
        chips = [item.strip() for item in (run.get("keywords") or [])[:4] if str(item).strip()]
    trend = ""
    if trend_signals:
        trend = _truncate_text(str(trend_signals[0].get("title") or "").strip(), 64)
    layout = "editorial_split"
    if len(title) > 74 or len(insight) > 150:
        layout = "stacked_brief"
    if len(chips) <= 2 and trend:
        layout = "signal_focus"
    return {
        "title": _truncate_text(title, 110),
        "insight": _truncate_text(insight, 180),
        "cta": _truncate_text(cta, 90),
        "chips": chips[:4],
        "trend": trend,
        "layout": layout,
    }


def _draw_chip(
    draw: ImageDraw.ImageDraw,
    x: int,
    y: int,
    text: str,
    fill: tuple[int, int, int],
    font: ImageFont.ImageFont,
) -> int:
    label = _truncate_text(text, 34)
    bbox = draw.textbbox((x, y), label, font=font)
    width = (bbox[2] - bbox[0]) + 30
    height = (bbox[3] - bbox[1]) + 18
    draw.rounded_rectangle((x, y, x + width, y + height), radius=18, fill=fill + (255,))
    draw.text((x + 15, y + 9), label, font=font, fill=(12, 24, 38))
    return int(width)


def _replace_placeholders(payload: Any, replacements: dict[str, str | int]) -> object:
    if isinstance(payload, dict):
        return {key: _replace_placeholders(value, replacements) for key, value in payload.items()}
    if isinstance(payload, list):
        return [_replace_placeholders(item, replacements) for item in payload]
    if isinstance(payload, str):
        value = payload
        for key, replacement in replacements.items():
            value = value.replace(f"{{{{{key}}}}}", str(replacement))
        if value.isdigit():
            try:
                return int(value)
            except ValueError:
                return value
        return value
    return payload


def _comfyui_request(
    url: str, path: str, payload: dict[str, Any] | None = None, timeout: int = 30
) -> dict[str, Any]:
    request = Request(  # noqa: S310
        f"{url.rstrip('/')}{path}",
        data=(json.dumps(payload).encode("utf-8") if payload is not None else None),
        headers={"Content-Type": "application/json"},
        method="POST" if payload is not None else "GET",
    )
    with urlopen(request, timeout=timeout) as response:  # noqa: S310
        body = response.read().decode("utf-8", errors="replace")
    return json.loads(body) if body else {}


def _download_comfyui_image(
    url: str, image_meta: dict[str, Any], output_path: Path, timeout: int = 60
) -> Path:
    query = urlencode(
        {
            "filename": image_meta.get("filename", ""),
            "subfolder": image_meta.get("subfolder", ""),
            "type": image_meta.get("type", "output"),
        }
    )
    request = Request(f"{url.rstrip('/')}/view?{query}")  # noqa: S310
    with urlopen(request, timeout=timeout) as response:  # noqa: S310
        content = response.read()
    output_path.write_bytes(content)
    return output_path


def _build_comfyui_image(run: dict[str, Any], output_path: Path, config: AppConfig) -> Path:
    if not config.comfyui_url:
        raise RuntimeError(
            "ComfyUI backend selected but OPENCLAW_SENTINEL_COMFYUI_URL is not configured."
        )
    workflow_template = load_json(config.comfyui_workflow_file, default={}) or {}
    if not workflow_template:
        raise RuntimeError(f"ComfyUI workflow template is missing: {config.comfyui_workflow_file}")

    prompt = _image_prompt_for_run(run)
    negative_prompt = (
        "blurry, distorted text, watermark, logo, low quality, artifact, duplicated subject"
    )
    width, height = _canvas_size(config)
    payload = _replace_placeholders(
        workflow_template,
        {
            "positive_prompt": prompt,
            "negative_prompt": negative_prompt,
            "seed": _seed_for_run(config, run),
            "width": width,
            "height": height,
        },
    )
    if not isinstance(payload, dict):
        raise RuntimeError("ComfyUI workflow template must resolve to a JSON object.")

    response = _comfyui_request(
        config.comfyui_url, "/prompt", {"prompt": payload}, timeout=max(30, config.http_timeout)
    )
    prompt_id = response.get("prompt_id", "")
    if not prompt_id:
        raise RuntimeError(f"ComfyUI did not return a prompt_id: {response}")

    for _ in range(45):
        history = _comfyui_request(
            config.comfyui_url, f"/history/{prompt_id}", timeout=max(30, config.http_timeout)
        )
        record = history.get(prompt_id) or {}
        outputs = record.get("outputs") or {}
        for node_output in outputs.values():
            images = node_output.get("images") or []
            if images:
                return _download_comfyui_image(
                    config.comfyui_url, images[0], output_path, timeout=60
                )
        time.sleep(2)

    raise RuntimeError(f"Timed out waiting for ComfyUI image generation for prompt_id={prompt_id}")


def _build_procedural_image(run: dict[str, Any], output_path: Path, config: AppConfig) -> Path:
    ensure_dir(output_path.parent)
    seed = _seed_for_run(config, run)
    # noqa: S311
    rng = random.Random(seed)  # noqa: S311

    canvas_size = _canvas_size(config)
    image = Image.new("RGBA", canvas_size, BACKGROUND_TOP + (255,))
    draw = ImageDraw.Draw(image)
    width, height = canvas_size
    _draw_gradient(draw, width, height)
    _draw_procedural_art(image, rng, _palette_for_run(config, run))

    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle((72, 58, width - 72, height - 58), radius=34, fill=CARD)
    draw.rounded_rectangle(
        (72, 58, 90, height - 58), radius=24, fill=_palette_for_run(config, run)[0] + (255,)
    )

    brand_font = _load_font(24, bold=True)
    title_font = _load_font(48, bold=True)
    body_font = _load_font(24)
    footer_font = _load_font(22)
    chip_font = _load_font(20, bold=True)

    visual = _visual_brief(run)
    title = str(visual["title"])
    summary = str(visual["insight"])
    chips = cast(list[dict[str, Any]], (run.get("chips") or []))
    keywords = ", ".join(str(chip.get("name", "")) for chip in chips)
    trend = f"Trend: {visual['trend']}".strip() if visual.get("trend") else ""
    cta = str(visual.get("cta") or "")
    layout = str(visual.get("layout") or "editorial_split")
    palette = _palette_for_run(config, run)

    draw.text((112, 92), "OPENCLAW CONTENT SENTINEL", font=brand_font, fill=palette[0] + (255,))

    y = 150
    title_width = 30 if layout != "stacked_brief" else 24
    for line in _wrap(title, title_width)[:4]:
        draw.text((112, y), line, font=title_font, fill=TEXT_MAIN)
        y += 58

    if layout == "editorial_split":
        insight_box = (width - 430, 126, width - 108, 320)
        draw.rounded_rectangle(insight_box, radius=28, fill=(255, 255, 255, 34))
        draw.text(
            (insight_box[0] + 26, insight_box[1] + 22),
            "Core insight",
            font=brand_font,
            fill=TEXT_MAIN,
        )
        insight_y = insight_box[1] + 62
        for line in _wrap(summary, 24)[:5]:
            draw.text((insight_box[0] + 26, insight_y), line, font=body_font, fill=TEXT_MUTED)
            insight_y += 30
    else:
        if summary:
            y += 8
            for line in _wrap(summary, 58 if layout == "signal_focus" else 50)[:4]:
                draw.text((112, y), line, font=body_font, fill=TEXT_MUTED)
                y += 34

    chip_y = height - 210 if layout == "editorial_split" else min(height - 210, y + 26)
    chip_x = 112
    for i, chip in enumerate(cast(list[dict[str, Any]], (run.get("chips") or []))):
        chip_width = _draw_chip(
            draw,
            chip_x,
            chip_y,
            str(chip.get("name", "")),
            palette[i % len(palette)],
            cast(ImageFont.ImageFont, chip_font),
        )
        chip_x += chip_width + 12
        if chip_x > width - 260:
            chip_x = 112
            chip_y += 52

    footer_y = height - 156
    backend_label = f"Image backend: {config.image_backend} | layout: {layout}"
    draw.text((112, footer_y), backend_label, font=footer_font, fill=TEXT_MUTED)
    footer_y += 30
    if keywords:
        draw.text((112, footer_y), f"Keywords: {keywords}", font=footer_font, fill=TEXT_MUTED)
        footer_y += 30
    if trend:
        for line in _wrap(trend, 74)[:2]:
            draw.text((112, footer_y), line, font=footer_font, fill=TEXT_MUTED)
            footer_y += 28
    if cta:
        cta_box = (width - 440, height - 158, width - 110, height - 82)
        draw.rounded_rectangle(cta_box, radius=22, fill=(255, 255, 255, 44))
        draw.text(
            (cta_box[0] + 18, cta_box[1] + 18),
            _truncate_text(cta, 58),
            font=footer_font,
            fill=TEXT_MAIN,
        )

    draw.text(
        (width - 330, height - 48), "Editorial original visual", font=footer_font, fill=TEXT_MAIN
    )
    image.convert("RGB").save(output_path, format="PNG")
    return output_path


def _carousel_slide_payloads(run: dict[str, Any]) -> list[dict[str, object]]:
    article = run.get("article") or {}
    analysis = run.get("analysis") or {}
    daily_input = run.get("daily_input") or {}
    trend_signals = run.get("trend_signals") or []
    visual = _visual_brief(run)
    trend_lines = [
        _truncate_text(str(item.get("title") or "").strip(), 78)
        for item in trend_signals[:3]
        if str(item.get("title") or "").strip()
    ]
    competitor_summary = _truncate_text(
        str(article.get("summary") or article.get("clean_text") or "").strip(),
        260,
    )
    original_angle = _truncate_text(str(analysis.get("original_angle") or "").strip(), 220)
    cta = _truncate_text(str(daily_input.get("key_call_to_action") or "").strip(), 120)
    return [
        {
            "label": "Slide 1",
            "heading": "The Signal",
            "title": str(visual.get("title") or "OpenClaw Content Sentinel"),
            "body": str(visual.get("insight") or competitor_summary or original_angle),
            "chips": list(cast(Any, visual.get("chips") or []))[:3],
        },
        {
            "label": "Slide 2",
            "heading": "Competitor Snapshot",
            "title": _truncate_text(str(article.get("title") or "Competitor article"), 92),
            "body": competitor_summary
            or "The competitor article supplied the baseline signal for this run.",
            "chips": [
                _truncate_text(
                    str(article.get("canonical_url") or article.get("url") or "public web source"),
                    34,
                ),
                _truncate_text(str(article.get("author") or "public source"), 34),
            ],
        },
        {
            "label": "Slide 3",
            "heading": "Trend Context",
            "title": "What public sources say right now",
            "body": "\n".join(f"- {line}" for line in trend_lines)
            or "- No public trend signal was strong enough to surface.",
            "chips": [
                str(item.get("source") or "").strip()
                for item in trend_signals[:3]
                if str(item.get("source") or "").strip()
            ],
        },
        {
            "label": "Slide 4",
            "heading": "Operator Move",
            "title": "Original response angle",
            "body": original_angle
            or (
                "Turn the research into a specific operator action with evidence "
                "and approval controls."
            ),
            "chips": [cta] if cta else list(cast(Any, visual.get("chips") or []))[:1],
        },
    ]


def _build_carousel_slide(
    run: dict[str, Any],
    output_path: Path,
    config: AppConfig,
    slide: dict[str, object],
    slide_index: int,
) -> Path:
    ensure_dir(output_path.parent)
    slide_config = replace(config, image_width=1080, image_height=1350)
    seed = _seed_for_run(slide_config, run) + (slide_index * 97)
    # noqa: S311
    rng = random.Random(seed)  # noqa: S311

    width, height = _canvas_size(slide_config)
    image = Image.new("RGBA", (width, height), BACKGROUND_TOP + (255,))
    draw = ImageDraw.Draw(image)
    _draw_gradient(draw, width, height)
    _draw_procedural_art(image, rng, _palette_for_run(slide_config, run))
    draw = ImageDraw.Draw(image)

    palette = _palette_for_run(slide_config, run)
    draw.rounded_rectangle((54, 54, width - 54, height - 54), radius=42, fill=CARD)
    draw.rounded_rectangle(
        (54, 54, 76, height - 54), radius=24, fill=palette[slide_index % len(palette)] + (255,)
    )

    eyebrow_font = _load_font(26, bold=True)
    title_font = _load_font(54, bold=True)
    body_font = _load_font(30)
    chip_font = _load_font(22, bold=True)
    footer_font = _load_font(22)

    draw.text(
        (96, 92),
        str(slide.get("label") or f"Slide {slide_index + 1}"),
        font=eyebrow_font,
        fill=palette[slide_index % len(palette)] + (255,),
    )
    draw.text((96, 132), str(slide.get("heading") or ""), font=eyebrow_font, fill=TEXT_MUTED)

    y = 210
    for line in _wrap(str(slide.get("title") or ""), 24)[:4]:
        draw.text((96, y), line, font=title_font, fill=TEXT_MAIN)
        y += 64

    y += 12
    for raw_line in str(slide.get("body") or "").splitlines():
        wrapped = _wrap(raw_line, 42)
        if not wrapped:
            y += 16
            continue
        for line in wrapped:
            draw.text((96, y), line, font=body_font, fill=TEXT_MUTED)
            y += 40
        y += 10

    chip_y = min(height - 170, y + 20)
    chip_x = 96
    for index, chip in enumerate(cast(list[Any], slide.get("chips") or [])):
        if not str(chip).strip():
            continue
        chip_width = _draw_chip(
            draw,
            chip_x,
            chip_y,
            str(chip),
            palette[(index + slide_index) % len(palette)],
            cast(ImageFont.ImageFont, chip_font),
        )
        chip_x += chip_width + 14
        if chip_x > width - 260:
            chip_x = 96
            chip_y += 56

    draw.text(
        (96, height - 92),
        "Generated from zero-cost research and competitor analysis",
        font=footer_font,
        fill=TEXT_MUTED,
    )
    image.convert("RGB").save(output_path, format="PNG")
    return output_path


def build_render_asset_pack(
    run: dict[str, Any],
    output_dir: Path,
    *,
    config: AppConfig,
    asset_type: str = "social_card",
    template_id: str = "",
    slide_count: int = 4,
) -> dict[str, object]:
    ensure_dir(output_dir)
    normalized_asset_type = str(asset_type or "social_card").strip().lower() or "social_card"
    resolved_template_id = str(template_id or "").strip() or {
        "social_card": "editorial_split_social_card",
        "cover_card": "cover_card_1600x900",
        "carousel": "carousel_standard_1080x1350",
    }.get(normalized_asset_type, "editorial_split_social_card")

    assets: list[str] = []
    primary_asset = ""

    if normalized_asset_type == "carousel":
        slides = _carousel_slide_payloads(run)[: max(1, min(int(slide_count or 4), 8))]
        for index, slide in enumerate(slides, start=1):
            slide_path = output_dir / f"slide-{index:02d}.png"
            _build_carousel_slide(run, slide_path, config, slide, index - 1)
            assets.append(str(slide_path))
        if assets:
            primary_asset = assets[0]
    else:
        image_name = (
            "cover_card.png" if normalized_asset_type == "cover_card" else "social_card.png"
        )
        target_path = output_dir / image_name
        if normalized_asset_type == "cover_card":
            cover_config = replace(config, image_width=1600, image_height=900)
            build_social_card(run, target_path, config=cover_config)
        else:
            build_social_card(run, target_path, config=config)
        assets.append(str(target_path))
        primary_asset = str(target_path)

    manifest = {
        "asset_type": normalized_asset_type,
        "template_id": resolved_template_id,
        "assets": [
            {
                "path": str(Path(asset)),
                "sha256": file_sha256(Path(asset)),
            }
            for asset in assets
        ],
        "primary_asset": primary_asset,
        "image_backend": config.image_backend,
        "generated_at": time.time(),
    }
    manifest_path = output_dir / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return {
        "asset_type": normalized_asset_type,
        "template_id": resolved_template_id,
        "assets": assets,
        "primary_asset": primary_asset,
        "manifest_path": str(manifest_path),
    }


def build_social_card(
    run: dict[str, Any], output_path: Path, config: AppConfig | None = None
) -> Path:
    ensure_dir(output_path.parent)
    config = config or AppConfig.from_env(output_path.parent.parent.parent)

    if config.image_backend == "comfyui_local":
        try:
            return _build_comfyui_image(run, output_path, config)
        except Exception:
            return _build_procedural_image(run, output_path, config)
    return _build_procedural_image(run, output_path, config)
