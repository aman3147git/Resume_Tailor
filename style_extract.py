"""Extract visual style hints from an uploaded resume PDF.

Pulls out things like:
  - dominant font family (serif / sans-serif)
  - the most likely accent color (used for headings / dividers)
  - section order (so we can mirror it in the rebuild)

These hints are passed to the renderer to make the output look like the
uploaded template.
"""

from __future__ import annotations

import io
import re
from collections import Counter
from dataclasses import dataclass, field


SERIF_HINTS = ("times", "garamond", "serif", "georgia", "minion", "palatino",
               "cambria", "baskerville", "constantia")
MONO_HINTS = ("mono", "courier", "consolas", "menlo", "ubuntu mono")

DEFAULT_ACCENT = "#0B3D91"


@dataclass
class StyleHints:
    font_family: str = "Helvetica"           # "Helvetica" or "Times-Roman"
    is_serif: bool = False
    accent_hex: str = DEFAULT_ACCENT
    section_order: list[str] = field(default_factory=list)
    detected: bool = False
    notes: list[str] = field(default_factory=list)


def extract_style_hints(pdf_bytes: bytes) -> StyleHints:
    """Best-effort style extraction. Returns sane defaults if anything fails."""
    hints = StyleHints()
    if not pdf_bytes:
        return hints
    try:
        import fitz  # PyMuPDF
    except Exception:
        hints.notes.append("pymupdf not available — using default style")
        return hints

    try:
        doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    except Exception as exc:
        hints.notes.append(f"Could not open PDF: {exc}")
        return hints

    font_counter: Counter[str] = Counter()
    color_sizes: dict[int, float] = {}
    section_headers: list[tuple[float, str, float]] = []

    try:
        for page in doc:
            data = page.get_text("dict")
            for block in data.get("blocks", []):
                if block.get("type", 0) != 0:
                    continue
                for line in block.get("lines", []):
                    for span in line.get("spans", []):
                        font = (span.get("font") or "").lower()
                        text = (span.get("text") or "").strip()
                        size = span.get("size", 0) or 0
                        color = span.get("color", 0)
                        if not text:
                            continue
                        font_counter[font] += len(text)
                        color_sizes[color] = color_sizes.get(color, 0) + size * len(text)
                        if _looks_like_section_header(text, size):
                            section_headers.append((size, text, color))
    finally:
        doc.close()

    if font_counter:
        dom_font = font_counter.most_common(1)[0][0]
        if any(h in dom_font for h in SERIF_HINTS):
            hints.font_family = "Times-Roman"
            hints.is_serif = True
        elif any(h in dom_font for h in MONO_HINTS):
            hints.font_family = "Courier"
        else:
            hints.font_family = "Helvetica"
        hints.detected = True
        hints.notes.append(f"Dominant font: {dom_font}")

    accent_int = _pick_accent_color(color_sizes)
    if accent_int is not None:
        hints.accent_hex = _int_to_hex(accent_int)
        hints.notes.append(f"Accent color detected: {hints.accent_hex}")

    if section_headers:
        seen = set()
        ordered: list[str] = []
        for _, text, _ in section_headers:
            norm = _normalize_section(text)
            if norm and norm not in seen:
                seen.add(norm)
                ordered.append(norm)
        hints.section_order = ordered

    return hints


_KNOWN_SECTIONS = {
    "summary", "profile", "objective", "about",
    "experience", "work experience", "professional experience", "employment",
    "education", "academic background",
    "skills", "technical skills", "technologies", "tech stack",
    "projects", "personal projects", "selected projects",
    "certifications", "certificates", "courses",
    "achievements", "awards", "honors",
    "publications", "research",
    "languages", "interests", "activities", "volunteer", "leadership",
}


def _normalize_section(text: str) -> str | None:
    cleaned = re.sub(r"[^a-z ]", "", text.lower()).strip()
    if cleaned in _KNOWN_SECTIONS:
        return cleaned.title()
    # Try first 2 words
    head = " ".join(cleaned.split()[:2])
    if head in _KNOWN_SECTIONS:
        return head.title()
    return None


def _looks_like_section_header(text: str, size: float) -> bool:
    if size < 11:
        return False
    if len(text) > 40:
        return False
    return _normalize_section(text) is not None


def _pick_accent_color(weighted: dict[int, float]) -> int | None:
    """Pick the largest non-black, non-near-white colored signal."""
    candidates = []
    for color_int, weight in weighted.items():
        r, g, b = _int_to_rgb(color_int)
        if max(r, g, b) > 240 and min(r, g, b) > 200:
            continue
        if r < 40 and g < 40 and b < 40:
            continue
        saturation = max(r, g, b) - min(r, g, b)
        if saturation < 30:
            continue
        candidates.append((weight, color_int))
    if not candidates:
        return None
    candidates.sort(reverse=True)
    return candidates[0][1]


def _int_to_rgb(color_int: int) -> tuple[int, int, int]:
    return (
        (color_int >> 16) & 0xFF,
        (color_int >> 8) & 0xFF,
        color_int & 0xFF,
    )


def _int_to_hex(color_int: int) -> str:
    r, g, b = _int_to_rgb(color_int)
    return f"#{r:02X}{g:02X}{b:02X}"


def hex_to_rgb_floats(hex_color: str) -> tuple[float, float, float]:
    h = hex_color.lstrip("#")
    return (
        int(h[0:2], 16) / 255.0,
        int(h[2:4], 16) / 255.0,
        int(h[4:6], 16) / 255.0,
    )
