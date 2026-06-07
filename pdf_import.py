"""Extract plain text from an uploaded resume PDF."""

from __future__ import annotations

import io
import re

from pypdf import PdfReader


def extract_text_from_pdf(data: bytes) -> str:
    """Return cleaned plain text from a PDF byte stream."""
    reader = PdfReader(io.BytesIO(data))
    if reader.is_encrypted:
        try:
            reader.decrypt("")
        except Exception as exc:
            raise RuntimeError(
                "PDF is password protected. Please remove the password and try again."
            ) from exc

    pages: list[str] = []
    for page in reader.pages:
        try:
            text = page.extract_text() or ""
        except Exception:
            text = ""
        if text.strip():
            pages.append(text)

    raw = "\n\n".join(pages)
    return _clean(raw)


def _clean(text: str) -> str:
    """Normalize whitespace and stray PDF artifacts."""
    text = text.replace("\x0c", "\n")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    lines = [ln.rstrip() for ln in text.splitlines()]
    return "\n".join(lines).strip()
