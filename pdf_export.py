"""Render the tailored markdown resume into a polished single-page PDF.

Uses reportlab Platypus with KeepInFrame(shrink) for guaranteed single-page
output. Accepts optional StyleHints (extracted from a user-uploaded template
PDF) to mimic font family and accent color.
"""

from __future__ import annotations

import io
import re
from dataclasses import dataclass

from reportlab.lib.colors import HexColor, black
from reportlab.lib.pagesizes import LETTER
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import inch
from reportlab.platypus import (
    BaseDocTemplate,
    Frame,
    PageTemplate,
    Paragraph,
    Spacer,
)
from reportlab.platypus.flowables import HRFlowable

from style_extract import StyleHints


@dataclass
class RenderResult:
    pdf: bytes
    scale: float
    clipped: bool
    pages: int


_PAGE_W, _PAGE_H = LETTER
_LEFT = 0.55 * inch
_RIGHT = 0.55 * inch
_TOP = 0.45 * inch
_BOTTOM = 0.45 * inch


def render_pdf(md_text: str, style: StyleHints | None = None,
               force_single_page: bool = True) -> dict:
    """Backwards-compatible entry point. Returns a dict with pdf bytes + metadata."""
    result = _render(md_text, style or StyleHints(), force_single_page)
    return {
        "pdf": result.pdf,
        "scale": result.scale,
        "clipped": result.clipped,
        "pages": result.pages,
    }


def markdown_to_pdf_bytes(md_text: str, force_single_page: bool = True,
                          style: StyleHints | None = None) -> bytes:
    return render_pdf(md_text, style=style, force_single_page=force_single_page)["pdf"]


def _render(md_text: str, style: StyleHints, force_single_page: bool) -> RenderResult:
    """Pick the largest scale that still fits the resume on a single page.

    Strategy:
      1. Render at scale 1.0.
      2. If it overflows, shrink (1.00 -> 0.60) until it fits.
      3. If it fits at 1.0, GROW (1.05, 1.10, ...) until it just barely overflows,
         then return the last successful larger scale. This fills the whole page
         when the model's output is short.
    """
    if not force_single_page:
        pdf, pages = _build(md_text, style, 1.0)
        return RenderResult(pdf=pdf, scale=1.0, clipped=False, pages=pages)

    base_pdf, base_pages = _build(md_text, style, 1.0)

    if base_pages > 1:
        last_pdf, last_pages, last_scale = base_pdf, base_pages, 1.0
        for scale in (0.97, 0.94, 0.91, 0.88, 0.85, 0.82, 0.79,
                      0.76, 0.73, 0.70, 0.67, 0.64, 0.60):
            try:
                pdf, pages = _build(md_text, style, scale)
            except Exception:
                continue
            last_pdf, last_pages, last_scale = pdf, pages, scale
            if pages <= 1:
                return RenderResult(pdf=pdf, scale=scale, clipped=False, pages=pages)
        return RenderResult(
            pdf=_trim_to_first_page(last_pdf),
            scale=last_scale,
            clipped=True,
            pages=last_pages,
        )

    # Fits at 1.0 — grow it to fill the page.
    best_pdf, best_scale = base_pdf, 1.0
    for scale in (1.04, 1.08, 1.12, 1.16, 1.20, 1.25, 1.30, 1.35, 1.40, 1.45, 1.50):
        try:
            pdf, pages = _build(md_text, style, scale)
        except Exception:
            break
        if pages > 1:
            break
        best_pdf, best_scale = pdf, scale
    return RenderResult(pdf=best_pdf, scale=best_scale, clipped=False, pages=1)


def _build(md_text: str, style: StyleHints, scale: float) -> tuple[bytes, int]:
    """Build a PDF at the given scale factor. Returns (bytes, page_count)."""
    styles = _build_styles(style, scale)
    story = _markdown_to_flowables(md_text, styles)

    buf = io.BytesIO()
    doc = BaseDocTemplate(
        buf,
        pagesize=LETTER,
        leftMargin=_LEFT,
        rightMargin=_RIGHT,
        topMargin=_TOP,
        bottomMargin=_BOTTOM,
        title="Tailored Resume",
        author="Resume Tailor",
    )

    frame = Frame(
        _LEFT, _BOTTOM,
        _PAGE_W - _LEFT - _RIGHT,
        _PAGE_H - _TOP - _BOTTOM,
        leftPadding=0, rightPadding=0, topPadding=0, bottomPadding=0,
        showBoundary=0,
    )

    page_counter = {"n": 0}

    def _count(_canvas, _doc) -> None:
        page_counter["n"] += 1

    doc.addPageTemplates([
        PageTemplate(id="single", frames=[frame], onPage=_count)
    ])
    doc.build(story)
    return buf.getvalue(), page_counter["n"]


def _build_styles(style: StyleHints, scale: float = 1.0) -> dict[str, ParagraphStyle]:
    """Build a stylesheet keyed by markdown element. All sizes / spacing are
    multiplied by `scale` so the layout shrinks proportionally when needed."""
    accent = HexColor(style.accent_hex) if style.accent_hex else HexColor("#0B3D91")

    if style.font_family == "Times-Roman":
        body_font = "Times-Roman"
        bold_font = "Times-Bold"
        italic_font = "Times-Italic"
        bold_italic_font = "Times-BoldItalic"
    elif style.font_family == "Courier":
        body_font = "Courier"
        bold_font = "Courier-Bold"
        italic_font = "Courier-Oblique"
        bold_italic_font = "Courier-BoldOblique"
    else:
        body_font = "Helvetica"
        bold_font = "Helvetica-Bold"
        italic_font = "Helvetica-Oblique"
        bold_italic_font = "Helvetica-BoldOblique"

    def s(v: float) -> float:
        return v * scale

    return {
        "name": ParagraphStyle(
            name="Name",
            fontName=bold_font,
            fontSize=s(20),
            leading=s(23),
            textColor=accent,
            spaceAfter=s(2),
            alignment=0,
        ),
        "contact": ParagraphStyle(
            name="Contact",
            fontName=body_font,
            fontSize=s(9.5),
            leading=s(12),
            textColor=HexColor("#444444"),
            spaceAfter=s(6),
        ),
        "h2": ParagraphStyle(
            name="H2",
            fontName=bold_font,
            fontSize=s(11.5),
            leading=s(14),
            textColor=accent,
            spaceBefore=s(8),
            spaceAfter=s(2),
        ),
        "h3": ParagraphStyle(
            name="H3",
            fontName=bold_font,
            fontSize=s(10.5),
            leading=s(13),
            textColor=HexColor("#1a1a1a"),
            spaceBefore=s(4),
            spaceAfter=0,
        ),
        "subhead": ParagraphStyle(
            name="Subhead",
            fontName=italic_font,
            fontSize=s(9.5),
            leading=s(11.5),
            textColor=HexColor("#555555"),
            spaceAfter=s(2),
        ),
        "body": ParagraphStyle(
            name="Body",
            fontName=body_font,
            fontSize=s(10),
            leading=s(12.5),
            textColor=black,
            spaceAfter=s(1),
        ),
        "bullet": ParagraphStyle(
            name="Bullet",
            fontName=body_font,
            fontSize=s(10),
            leading=s(12.5),
            textColor=black,
            leftIndent=s(10),
            firstLineIndent=s(-10),
            bulletIndent=0,
            spaceAfter=0,
        ),
        "accent_color": accent,
        "_bold_font": bold_font,
        "_italic_font": italic_font,
        "_body_font": body_font,
        "_bold_italic_font": bold_italic_font,
        "_scale": scale,
    }


_INLINE_BOLD = re.compile(r"\*\*([^*]+)\*\*")
_INLINE_ITALIC = re.compile(r"(?<!\*)\*([^*]+)\*(?!\*)")
_INLINE_CODE = re.compile(r"`([^`]+)`")
_INLINE_LINK = re.compile(r"\[([^\]]+)\]\(([^)]+)\)")


def _md_inline_to_html(text: str) -> str:
    """Convert inline markdown to reportlab-compatible HTML."""
    s = text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    s = _INLINE_LINK.sub(r'<link href="\2" color="#0B3D91">\1</link>', s)
    s = _INLINE_BOLD.sub(r"<b>\1</b>", s)
    s = _INLINE_ITALIC.sub(r"<i>\1</i>", s)
    s = _INLINE_CODE.sub(r'<font face="Courier">\1</font>', s)
    return s


def _markdown_to_flowables(md_text: str, styles: dict) -> list:
    """Turn the tailored markdown into a list of reportlab Flowables."""
    flowables: list = []
    lines = md_text.splitlines()

    contact_collected = False
    first_h2_after_name_pending = False
    name_done = False

    i = 0
    while i < len(lines):
        line = lines[i].rstrip()
        stripped = line.strip()

        if not stripped:
            i += 1
            continue

        h_match = re.match(r"^(#{1,6})\s+(.*)$", line)
        if h_match:
            level = len(h_match.group(1))
            text = h_match.group(2).strip()
            html = _md_inline_to_html(text)

            if level == 1 or (not name_done and level == 2):
                flowables.append(Paragraph(html, styles["name"]))
                flowables.append(HRFlowable(
                    width="100%", thickness=1.0,
                    color=styles["accent_color"], spaceBefore=1, spaceAfter=2,
                ))
                name_done = True
                contact_collected = False
                # The next non-empty, non-header line is the contact line.
                j = i + 1
                while j < len(lines) and not lines[j].strip():
                    j += 1
                if j < len(lines) and not re.match(r"^#+\s+", lines[j]):
                    contact_html = _md_inline_to_html(lines[j].strip())
                    flowables.append(Paragraph(contact_html, styles["contact"]))
                    i = j
                    contact_collected = True
                i += 1
                continue

            if level == 2:
                flowables.append(Paragraph(html.upper(), styles["h2"]))
                flowables.append(HRFlowable(
                    width="100%", thickness=0.4,
                    color=HexColor("#999999"), spaceBefore=0, spaceAfter=2,
                ))
                i += 1
                continue

            if level == 3:
                flowables.append(Paragraph(html, styles["h3"]))
                # Peek for an italic subhead line on the next non-empty row.
                j = i + 1
                while j < len(lines) and not lines[j].strip():
                    j += 1
                if j < len(lines):
                    nxt = lines[j].strip()
                    if (nxt.startswith("*") and nxt.endswith("*")
                            and not nxt.startswith("**")):
                        flowables.append(Paragraph(
                            _md_inline_to_html(nxt.strip("*").strip()),
                            styles["subhead"],
                        ))
                        i = j
                i += 1
                continue

            # h4+ falls back to body
            flowables.append(Paragraph(html, styles["body"]))
            i += 1
            continue

        bullet_match = re.match(r"^[-*+]\s+(.*)$", stripped)
        if bullet_match:
            text = bullet_match.group(1)
            html = _md_inline_to_html(text)
            flowables.append(Paragraph(html, styles["bullet"], bulletText="\u2022"))
            i += 1
            continue

        num_bullet = re.match(r"^\d+\.\s+(.*)$", stripped)
        if num_bullet:
            text = num_bullet.group(1)
            flowables.append(Paragraph(_md_inline_to_html(text),
                                        styles["bullet"], bulletText="\u2022"))
            i += 1
            continue

        if stripped == "---":
            flowables.append(HRFlowable(
                width="100%", thickness=0.4,
                color=HexColor("#cccccc"), spaceBefore=2, spaceAfter=2,
            ))
            i += 1
            continue

        if stripped.startswith("*") and stripped.endswith("*") and not stripped.startswith("**"):
            flowables.append(Paragraph(
                _md_inline_to_html(stripped.strip("*").strip()),
                styles["subhead"],
            ))
            i += 1
            continue

        if not contact_collected and name_done:
            flowables.append(Paragraph(_md_inline_to_html(stripped),
                                        styles["contact"]))
            contact_collected = True
            i += 1
            continue

        flowables.append(Paragraph(_md_inline_to_html(stripped), styles["body"]))
        i += 1

    return flowables


def _trim_to_first_page(pdf_bytes: bytes) -> bytes:
    """Keep only the first page of an existing PDF."""
    if not pdf_bytes:
        return pdf_bytes
    try:
        from pypdf import PdfReader, PdfWriter
        reader = PdfReader(io.BytesIO(pdf_bytes))
        writer = PdfWriter()
        if reader.pages:
            writer.add_page(reader.pages[0])
        out = io.BytesIO()
        writer.write(out)
        return out.getvalue()
    except Exception:
        return pdf_bytes
