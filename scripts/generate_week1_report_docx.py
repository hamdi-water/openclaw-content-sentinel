from __future__ import annotations

import re
from collections.abc import Iterable
from pathlib import Path

from docx import Document
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Inches, Pt
from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[1]
DOCS_DIR = ROOT / "docs"
ASSETS_DIR = DOCS_DIR / "generated-assets"
SOURCE_MD = DOCS_DIR / "week1-architecture-report.md"
OUTPUT_DOCX = DOCS_DIR / "complete-week-1-report.docx"


def ensure_dirs() -> None:
    ASSETS_DIR.mkdir(parents=True, exist_ok=True)


def load_font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    font_candidates = []
    if bold:
        font_candidates.extend(
            [
                Path("C:/Windows/Fonts/calibrib.ttf"),
                Path("C:/Windows/Fonts/arialbd.ttf"),
                Path("C:/Windows/Fonts/segoeuib.ttf"),
            ]
        )
    else:
        font_candidates.extend(
            [
                Path("C:/Windows/Fonts/calibri.ttf"),
                Path("C:/Windows/Fonts/arial.ttf"),
                Path("C:/Windows/Fonts/segoeui.ttf"),
            ]
        )
    for candidate in font_candidates:
        if candidate.exists():
            return ImageFont.truetype(str(candidate), size=size)
    return ImageFont.load_default()


def wrap_text(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.ImageFont, max_width: int) -> list[str]:
    words = text.split()
    lines: list[str] = []
    current = ""
    for word in words:
        candidate = word if not current else f"{current} {word}"
        if draw.textlength(candidate, font=font) <= max_width:
            current = candidate
        else:
            if current:
                lines.append(current)
            current = word
    if current:
        lines.append(current)
    return lines or [text]


def draw_box(
    draw: ImageDraw.ImageDraw,
    xy: tuple[int, int, int, int],
    text: str,
    fill: str,
    outline: str = "#1f2937",
    radius: int = 20,
    font_size: int = 28,
    bold: bool = False,
) -> None:
    draw.rounded_rectangle(xy, radius=radius, fill=fill, outline=outline, width=3)
    font = load_font(font_size, bold=bold)
    left, top, right, bottom = xy
    max_width = right - left - 30
    lines = wrap_text(draw, text, font, max_width)
    line_height = font_size + 8
    total_height = len(lines) * line_height
    y = top + ((bottom - top) - total_height) // 2
    for line in lines:
        text_width = int(draw.textlength(line, font=font))
        x = left + ((right - left) - text_width) // 2
        draw.text((x, y), line, fill="#0f172a", font=font)
        y += line_height


def draw_arrow(
    draw: ImageDraw.ImageDraw,
    start: tuple[int, int],
    end: tuple[int, int],
    color: str = "#334155",
    width: int = 5,
) -> None:
    draw.line([start, end], fill=color, width=width)
    dx = end[0] - start[0]
    dy = end[1] - start[1]
    if dx == 0 and dy == 0:
        return
    import math

    angle = math.atan2(dy, dx)
    arrow_length = 18
    arrow_angle = math.pi / 8
    p1 = (
        int(end[0] - arrow_length * math.cos(angle - arrow_angle)),
        int(end[1] - arrow_length * math.sin(angle - arrow_angle)),
    )
    p2 = (
        int(end[0] - arrow_length * math.cos(angle + arrow_angle)),
        int(end[1] - arrow_length * math.sin(angle + arrow_angle)),
    )
    draw.polygon([end, p1, p2], fill=color)


def draw_diamond(
    draw: ImageDraw.ImageDraw,
    center: tuple[int, int],
    width: int,
    height: int,
    text: str,
    fill: str,
) -> None:
    cx, cy = center
    points = [
        (cx, cy - height // 2),
        (cx + width // 2, cy),
        (cx, cy + height // 2),
        (cx - width // 2, cy),
    ]
    draw.polygon(points, fill=fill, outline="#1f2937")
    font = load_font(24, bold=True)
    lines = wrap_text(draw, text, font, width - 40)
    y = cy - (len(lines) * 16)
    for line in lines:
        text_width = int(draw.textlength(line, font=font))
        draw.text((cx - text_width // 2, y), line, fill="#0f172a", font=font)
        y += 32


def add_caption(draw: ImageDraw.ImageDraw, text: str, width: int, y: int) -> None:
    font = load_font(40, bold=True)
    text_width = int(draw.textlength(text, font=font))
    draw.text(((width - text_width) // 2, y), text, fill="#0f172a", font=font)


def generate_runtime_topology(path: Path) -> None:
    width, height = 1800, 1100
    image = Image.new("RGB", (width, height), "#f8fafc")
    draw = ImageDraw.Draw(image)

    add_caption(draw, "Figure 1. OpenClaw Content Sentinel Runtime Topology", width, 30)

    draw_box(draw, (700, 120, 1100, 220), "OpenClaw Gateway", "#bfdbfe", font_size=34, bold=True)
    draw_box(draw, (120, 280, 420, 380), "Telegram Bot Interface", "#fde68a")
    draw_box(draw, (500, 280, 800, 380), "OpenClaw Cron", "#c7d2fe")
    draw_box(draw, (1000, 280, 1300, 380), "OpenClaw Heartbeat", "#c7d2fe")
    draw_box(draw, (1380, 280, 1680, 380), "Browser Profiles", "#fed7aa")

    draw_box(draw, (420, 470, 760, 580), "Workspace Skills", "#bbf7d0", font_size=32, bold=True)
    draw_box(draw, (1040, 470, 1380, 580), "Workspace Memory", "#ddd6fe", font_size=32, bold=True)

    draw_box(draw, (80, 680, 380, 790), "Competitor Ingestion", "#dcfce7")
    draw_box(draw, (420, 680, 720, 790), "Trend Research", "#dcfce7")
    draw_box(draw, (760, 680, 1060, 790), "Analysis and Drafting", "#dcfce7")
    draw_box(draw, (1100, 680, 1400, 790), "Image Generation", "#dcfce7")
    draw_box(draw, (1440, 680, 1740, 790), "Approval and Posting", "#dcfce7")

    draw_box(draw, (280, 900, 700, 1000), "Source Cache and Evidence Store", "#e2e8f0")
    draw_box(draw, (820, 900, 1160, 1000), "Draft Artifacts", "#e2e8f0")
    draw_box(draw, (1280, 900, 1620, 1000), "Run Logs and Screenshots", "#e2e8f0")

    for x in (270, 650, 1150, 1530):
        draw_arrow(draw, (900, 220), (x, 280))
    draw_arrow(draw, (900, 220), (590, 470))
    draw_arrow(draw, (900, 220), (1210, 470))

    for x in (230, 570, 910, 1250, 1590):
        draw_arrow(draw, (590, 580), (x, 680))

    draw_arrow(draw, (230, 790), (490, 900))
    draw_arrow(draw, (570, 790), (490, 900))
    draw_arrow(draw, (910, 790), (990, 900))
    draw_arrow(draw, (1250, 790), (990, 900))
    draw_arrow(draw, (1590, 790), (1450, 900))
    draw_arrow(draw, (270, 330), (1450, 900))

    image.save(path)


def generate_execution_flow(path: Path) -> None:
    width, height = 1800, 1200
    image = Image.new("RGB", (width, height), "#fffdf8")
    draw = ImageDraw.Draw(image)

    add_caption(draw, "Figure 2. Daily Execution and Approval Flow", width, 30)

    draw_box(draw, (650, 120, 1150, 220), "Daily Cron Trigger", "#c7d2fe", font_size=34, bold=True)
    draw_box(draw, (600, 280, 1200, 380), "Fetch Prompt and Competitor URL", "#fde68a", font_size=30)
    draw_box(draw, (560, 440, 1240, 540), "Scrape, Clean, and Summarize Source", "#fde68a", font_size=30)
    draw_box(draw, (600, 600, 1200, 700), "Trend Research Pipeline", "#bfdbfe", font_size=30)
    draw_box(draw, (560, 760, 1240, 860), "Angle Selection and Confidence Score", "#bfdbfe", font_size=30)
    draw_box(draw, (520, 920, 1280, 1020), "Draft Article, Social Variants, and Image Prompt", "#bbf7d0", font_size=28)
    draw_box(draw, (660, 1080, 1140, 1160), "Telegram Preview", "#fed7aa", font_size=30, bold=True)

    draw_diamond(draw, (900, 1320), 420, 180, "Approved?", "#fecaca")
    draw_box(draw, (180, 1460, 540, 1560), "Hold for Edits", "#f5d0fe", font_size=30)
    draw_box(draw, (720, 1460, 1080, 1560), "Post to LinkedIn", "#dcfce7", font_size=30)
    draw_box(draw, (1120, 1460, 1480, 1560), "Post to Facebook", "#dcfce7", font_size=30)
    draw_box(draw, (1520, 1460, 1760, 1560), "Post to X", "#dcfce7", font_size=30)
    draw_box(draw, (860, 1680, 1360, 1780), "Persist Logs and Artifacts", "#e2e8f0", font_size=30)

    # The canvas is shorter than the drawn content; crop after drawing.
    full = Image.new("RGB", (width, 1820), "#fffdf8")
    full.paste(image, (0, 0))
    draw = ImageDraw.Draw(full)

    add_caption(draw, "Figure 2. Daily Execution and Approval Flow", width, 30)
    draw_box(draw, (650, 120, 1150, 220), "Daily Cron Trigger", "#c7d2fe", font_size=34, bold=True)
    draw_box(draw, (600, 280, 1200, 380), "Fetch Prompt and Competitor URL", "#fde68a", font_size=30)
    draw_box(draw, (560, 440, 1240, 540), "Scrape, Clean, and Summarize Source", "#fde68a", font_size=30)
    draw_box(draw, (600, 600, 1200, 700), "Trend Research Pipeline", "#bfdbfe", font_size=30)
    draw_box(draw, (560, 760, 1240, 860), "Angle Selection and Confidence Score", "#bfdbfe", font_size=30)
    draw_box(draw, (520, 920, 1280, 1020), "Draft Article, Social Variants, and Image Prompt", "#bbf7d0", font_size=28)
    draw_box(draw, (660, 1080, 1140, 1160), "Telegram Preview", "#fed7aa", font_size=30, bold=True)
    draw_diamond(draw, (900, 1320), 420, 180, "Approved?", "#fecaca")
    draw_box(draw, (180, 1460, 540, 1560), "Hold for Edits", "#f5d0fe", font_size=30)
    draw_box(draw, (720, 1460, 1080, 1560), "Post to LinkedIn", "#dcfce7", font_size=30)
    draw_box(draw, (1120, 1460, 1480, 1560), "Post to Facebook", "#dcfce7", font_size=30)
    draw_box(draw, (1520, 1460, 1760, 1560), "Post to X", "#dcfce7", font_size=30)
    draw_box(draw, (860, 1680, 1360, 1780), "Persist Logs and Artifacts", "#e2e8f0", font_size=30)

    draw_arrow(draw, (900, 220), (900, 280))
    draw_arrow(draw, (900, 380), (900, 440))
    draw_arrow(draw, (900, 540), (900, 600))
    draw_arrow(draw, (900, 700), (900, 760))
    draw_arrow(draw, (900, 860), (900, 920))
    draw_arrow(draw, (900, 1020), (900, 1080))
    draw_arrow(draw, (900, 1160), (900, 1230))
    draw_arrow(draw, (900, 1410), (900, 1460))
    draw_arrow(draw, (900, 1410), (360, 1460))
    draw_arrow(draw, (900, 1560), (1110, 1680))
    draw_arrow(draw, (1300, 1560), (1170, 1680))
    draw_arrow(draw, (1640, 1560), (1230, 1680))
    draw.text((930, 1420), "Yes", fill="#0f172a", font=load_font(24, bold=True))
    draw.text((590, 1420), "No", fill="#0f172a", font=load_font(24, bold=True))

    full.save(path)


def set_document_defaults(document: Document) -> None:
    section = document.sections[0]
    section.top_margin = Inches(0.8)
    section.bottom_margin = Inches(0.8)
    section.left_margin = Inches(0.8)
    section.right_margin = Inches(0.8)

    styles = document.styles
    styles["Normal"].font.name = "Calibri"
    styles["Normal"].font.size = Pt(11)
    for style_name, size in [("Heading 1", 18), ("Heading 2", 15), ("Heading 3", 12)]:
        styles[style_name].font.name = "Calibri"
        styles[style_name].font.size = Pt(size)


def add_page_number(section) -> None:
    footer = section.footer
    paragraph = footer.paragraphs[0]
    paragraph.alignment = WD_ALIGN_PARAGRAPH.CENTER
    run = paragraph.add_run()
    fld_char_begin = OxmlElement("w:fldChar")
    fld_char_begin.set(qn("w:fldCharType"), "begin")
    instr_text = OxmlElement("w:instrText")
    instr_text.set(qn("xml:space"), "preserve")
    instr_text.text = "PAGE"
    fld_char_end = OxmlElement("w:fldChar")
    fld_char_end.set(qn("w:fldCharType"), "end")
    run._r.append(fld_char_begin)
    run._r.append(instr_text)
    run._r.append(fld_char_end)


def add_cover(document: Document) -> None:
    p = document.add_paragraph()
    p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    run = p.add_run("OpenClaw Content Sentinel")
    run.bold = True
    run.font.size = Pt(24)

    p = document.add_paragraph()
    p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    run = p.add_run("Complete Week 1 Report")
    run.bold = True
    run.font.size = Pt(20)

    p = document.add_paragraph()
    p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    p.add_run("Architecture, zero-cost strategy, risk analysis, and visual review").italic = True

    for text in [
        "Prepared for: Intern Project Assignment",
        "Prepared by: Codex",
        "Date: March 25, 2026",
    ]:
        p = document.add_paragraph()
        p.alignment = WD_ALIGN_PARAGRAPH.CENTER
        p.add_run(text)

    document.add_page_break()


def flush_paragraph(document: Document, lines: list[str]) -> None:
    if not lines:
        return
    text = " ".join(line.strip() for line in lines).strip()
    if text:
        document.add_paragraph(text)
    lines.clear()


def parse_table_row(row: str) -> list[str]:
    return [cell.strip() for cell in row.strip().strip("|").split("|")]


def add_table(document: Document, rows: list[str]) -> None:
    parsed = [parse_table_row(row) for row in rows if row.strip()]
    if len(parsed) < 2:
        return
    header = parsed[0]
    body = [row for row in parsed[2:]]
    table = document.add_table(rows=1, cols=len(header))
    table.style = "Table Grid"
    for index, cell in enumerate(header):
        table.rows[0].cells[index].text = cell
    for row in body:
        cells = table.add_row().cells
        for index, cell in enumerate(row):
            cells[index].text = cell
    document.add_paragraph()


def add_code_block(document: Document, code_lines: Iterable[str]) -> None:
    paragraph = document.add_paragraph()
    for line in code_lines:
        run = paragraph.add_run(f"{line}\n")
        run.font.name = "Consolas"
        run.font.size = Pt(9)


def build_document(runtime_img: Path, flow_img: Path) -> None:
    document = Document()
    set_document_defaults(document)
    add_page_number(document.sections[0])
    add_cover(document)

    lines = SOURCE_MD.read_text(encoding="utf-8").splitlines()
    paragraph_buffer: list[str] = []
    code_buffer: list[str] = []
    table_buffer: list[str] = []
    in_code = False
    code_lang = ""
    mermaid_index = 0

    def flush_table() -> None:
        nonlocal table_buffer
        if table_buffer:
            add_table(document, table_buffer)
            table_buffer = []

    for line in lines:
        stripped = line.strip()
        if stripped.startswith("```"):
            flush_paragraph(document, paragraph_buffer)
            flush_table()
            if not in_code:
                in_code = True
                code_lang = stripped[3:].strip()
                code_buffer = []
            else:
                in_code = False
                if code_lang == "mermaid":
                    mermaid_index += 1
                    image_path = runtime_img if mermaid_index == 1 else flow_img
                    document.add_picture(str(image_path), width=Inches(6.8))
                    cap = document.add_paragraph("Rendered architecture diagram")
                    cap.alignment = WD_ALIGN_PARAGRAPH.CENTER
                else:
                    add_code_block(document, code_buffer)
                code_buffer = []
                code_lang = ""
            continue

        if in_code:
            code_buffer.append(line)
            continue

        if stripped.startswith("|") and stripped.endswith("|"):
            flush_paragraph(document, paragraph_buffer)
            table_buffer.append(line)
            continue
        else:
            flush_table()

        if not stripped:
            flush_paragraph(document, paragraph_buffer)
            continue

        heading_match = re.match(r"^(#{1,3})\s+(.*)$", line)
        if heading_match:
            flush_paragraph(document, paragraph_buffer)
            level = len(heading_match.group(1))
            document.add_heading(heading_match.group(2).strip(), level=level)
            continue

        if re.match(r"^\d+\.\s+", stripped):
            flush_paragraph(document, paragraph_buffer)
            document.add_paragraph(re.sub(r"^\d+\.\s+", "", stripped), style="List Number")
            continue

        if re.match(r"^-\s+", stripped):
            flush_paragraph(document, paragraph_buffer)
            document.add_paragraph(re.sub(r"^-\s+", "", stripped), style="List Bullet")
            continue

        if re.match(r"^\s+-\s+", line):
            flush_paragraph(document, paragraph_buffer)
            document.add_paragraph(re.sub(r"^\s+-\s+", "", line), style="List Bullet 2")
            continue

        paragraph_buffer.append(line)

    flush_paragraph(document, paragraph_buffer)
    flush_table()

    document.save(str(OUTPUT_DOCX))


def main() -> None:
    ensure_dirs()
    runtime_img = ASSETS_DIR / "figure-runtime-topology.png"
    flow_img = ASSETS_DIR / "figure-daily-flow.png"
    generate_runtime_topology(runtime_img)
    generate_execution_flow(flow_img)
    build_document(runtime_img, flow_img)
    print(f"Created {OUTPUT_DOCX}")


if __name__ == "__main__":
    main()
