"""Provider-agnostic LLM client for resume tailoring.

Supports:
  - Anthropic Claude
  - OpenAI GPT

Both providers expose the same streaming + non-streaming interface so the UI
layer doesn't need to care which one is in use.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from typing import Iterator, Literal

from prompt import SYSTEM_PROMPT, build_user_prompt
from skill_dependencies import filter_exploring_items


Provider = Literal["anthropic", "openai"]

DEFAULT_MAX_TOKENS = 4096

# Hard cap on the Currently Exploring bullet — keep only the top-N
# JD-critical items after the implicit-prerequisite filter has run.
# The model is already prompted to obey this (rule 23) but we enforce
# it deterministically so a misbehaving model can't slip a long list in.
MAX_EXPLORING_ITEMS = 4

DEFAULT_MODELS: dict[Provider, str] = {
    "anthropic": "claude-sonnet-4-5",
    "openai": "gpt-4o",
}

AVAILABLE_MODELS: dict[Provider, list[str]] = {
    "anthropic": [
        "claude-sonnet-4-5",
        "claude-opus-4-5",
        "claude-haiku-4-5",
        "claude-3-7-sonnet-latest",
        "claude-3-5-sonnet-latest",
        "claude-3-5-haiku-latest",
    ],
    "openai": [
        "gpt-4o",
        "gpt-4o-mini",
        "gpt-4.1",
        "gpt-4.1-mini",
        "gpt-4-turbo",
        "o4-mini",
    ],
}

ENV_KEYS: dict[Provider, str] = {
    "anthropic": "ANTHROPIC_API_KEY",
    "openai": "OPENAI_API_KEY",
}


@dataclass
class TailoredOutput:
    raw: str
    match_analysis: str
    tailored_resume: str
    removed_implicit: list[str] = field(default_factory=list)


def _resolve_key(provider: Provider, api_key: str | None) -> str:
    key = (api_key or os.getenv(ENV_KEYS[provider]) or "").strip()
    if not key:
        raise RuntimeError(
            f"Missing {ENV_KEYS[provider]}. "
            f"Set it in the sidebar or in .env."
        )
    return key


def stream_tailored_resume(
    master_resume: str,
    job_description: str,
    provider: Provider = "anthropic",
    api_key: str | None = None,
    model: str | None = None,
    max_tokens: int = DEFAULT_MAX_TOKENS,
) -> Iterator[str]:
    """Yield text chunks from the chosen provider."""
    model = model or DEFAULT_MODELS[provider]
    user_prompt = build_user_prompt(master_resume, job_description)
    key = _resolve_key(provider, api_key)

    if provider == "anthropic":
        yield from _stream_anthropic(
            key=key,
            model=model,
            max_tokens=max_tokens,
            user_prompt=user_prompt,
        )
    elif provider == "openai":
        yield from _stream_openai(
            key=key,
            model=model,
            max_tokens=max_tokens,
            user_prompt=user_prompt,
        )
    else:
        raise ValueError(f"Unknown provider: {provider}")


def generate_tailored_resume(
    master_resume: str,
    job_description: str,
    provider: Provider = "anthropic",
    api_key: str | None = None,
    model: str | None = None,
    max_tokens: int = DEFAULT_MAX_TOKENS,
) -> TailoredOutput:
    """Non-streaming convenience wrapper."""
    chunks = list(
        stream_tailored_resume(
            master_resume=master_resume,
            job_description=job_description,
            provider=provider,
            api_key=api_key,
            model=model,
            max_tokens=max_tokens,
        )
    )
    return parse_output("".join(chunks), master_resume=master_resume)


def _stream_anthropic(
    key: str, model: str, max_tokens: int, user_prompt: str
) -> Iterator[str]:
    from anthropic import Anthropic

    client = Anthropic(api_key=key)
    with client.messages.stream(
        model=model,
        max_tokens=max_tokens,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": user_prompt}],
    ) as stream:
        for text in stream.text_stream:
            yield text


def _stream_openai(
    key: str, model: str, max_tokens: int, user_prompt: str
) -> Iterator[str]:
    from openai import OpenAI

    client = OpenAI(api_key=key)
    response = client.chat.completions.create(
        model=model,
        max_tokens=max_tokens,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_prompt},
        ],
        stream=True,
    )
    for event in response:
        if not event.choices:
            continue
        delta = event.choices[0].delta
        text = getattr(delta, "content", None)
        if text:
            yield text


_MATCH_HEADER = re.compile(r"^#\s*Match Analysis\s*$", re.IGNORECASE | re.MULTILINE)
_RESUME_HEADER = re.compile(r"^#\s*Tailored Resume\s*$", re.IGNORECASE | re.MULTILINE)


def parse_output(raw: str, master_resume: str = "") -> TailoredOutput:
    """Split the raw model response into Match Analysis + Tailored Resume sections.

    `master_resume` (optional) is used to compute the candidate's known skills so
    we can filter implicit prerequisites out of the Currently Exploring line.
    Without it, only skills in the tailored output itself are considered known.
    """
    match_start = _MATCH_HEADER.search(raw)
    resume_start = _RESUME_HEADER.search(raw)

    if match_start and resume_start:
        match_analysis = raw[match_start.end(): resume_start.start()].strip()
        tailored_resume = raw[resume_start.end():].strip()
    elif resume_start:
        match_analysis = raw[: resume_start.start()].strip()
        tailored_resume = raw[resume_start.end():].strip()
    else:
        match_analysis = ""
        tailored_resume = raw.strip()

    tailored_resume, removed_implicit = _clean_tailored_resume(
        tailored_resume, master_resume=master_resume,
    )

    return TailoredOutput(
        raw=raw,
        match_analysis=match_analysis,
        tailored_resume=tailored_resume,
        removed_implicit=removed_implicit,
    )


_PLACEHOLDER_RE = re.compile(
    r"\[(?:include|add|insert|tbd|fill|todo|optional|note)[^\]]*\]",
    re.IGNORECASE,
)
_PARENTHETICAL_NOTE_RE = re.compile(
    r"\((?:if applicable|if available|only if|optional)[^)]*\)",
    re.IGNORECASE,
)
_META_PHRASES = (
    "the tailored resume",
    "this resume",
    "this tailored resume",
    "the above resume",
    "this output",
    "note that",
    "as requested",
    "let me know",
)


def _clean_tailored_resume(
    md: str, master_resume: str = ""
) -> tuple[str, list[str]]:
    """Post-process the model's resume markdown to remove common slip-ups:

      - lines that are pure placeholder text ([Include ...], [TBD], etc.)
      - empty sections (header followed by no real content or only a placeholder)
      - trailing meta-commentary paragraphs about the resume itself
      - "(if available)" / "(only if)" parenthetical notes
      - implicit prerequisites in the Currently Exploring line
        (e.g. don't list HTML if the candidate has React)

    Returns (cleaned_markdown, removed_implicit_skills).
    """
    if not md:
        return md, []

    raw_lines = md.splitlines()

    # Strip inline placeholder markers and parenthetical hints.
    lines: list[str] = []
    for line in raw_lines:
        cleaned = _PLACEHOLDER_RE.sub("", line)
        cleaned = _PARENTHETICAL_NOTE_RE.sub("", cleaned)
        cleaned = re.sub(r"[ \t]+", " ", cleaned).rstrip()
        lines.append(cleaned)

    # Drop bullet lines that became empty after stripping (e.g., "- [Include ...]").
    pruned: list[str] = []
    for line in lines:
        stripped = line.strip()
        if re.match(r"^[-*+]\s*$", stripped):
            continue
        pruned.append(line)

    # Strip trailing meta-commentary: walk backwards skipping blanks, drop any
    # tail paragraph that looks like commentary.
    while pruned:
        tail = pruned[-1].strip()
        if not tail:
            pruned.pop()
            continue
        if tail.startswith("#") or tail.startswith("-") or tail.startswith("*"):
            break
        lowered = tail.lower()
        if any(p in lowered for p in _META_PHRASES):
            pruned.pop()
            continue
        # Plain prose at the very end of a resume is almost certainly commentary.
        if len(tail) > 60 and not re.match(r"^\*[^*]+\*$", tail):
            pruned.pop()
            continue
        break

    # Filter implicit prerequisites out of Currently Exploring.
    pruned, removed_implicit = _filter_currently_exploring(pruned, master_resume)

    # Remove empty sections: a `##`/`###` header whose body has no real content
    # before the next header.
    pruned = _drop_empty_sections(pruned)

    return "\n".join(pruned).strip(), removed_implicit


_SKILLS_HEADER_RE = re.compile(
    r"^##\s+(?:Technical\s+Skills|Skills|Technologies?|Tech\s+Stack|"
    r"Core\s+Competencies)\s*$",
    re.IGNORECASE | re.MULTILINE,
)
_NEXT_SECTION_RE = re.compile(r"^##\s+", re.MULTILINE)
_BULLET_LABEL_RE = re.compile(r"\*\*[^*]+?\*\*:?\s*(.*)$")
_EXPLORING_RE = re.compile(
    r"^(\s*[-*+]\s+\*\*\s*Currently\s+Exploring\s*:?\s*\*\*\s*:?\s*)(.*)$",
    re.IGNORECASE,
)


def extract_known_skills(md: str) -> set[str]:
    """Extract every skill listed in the Technical Skills section of a resume.

    Skips the `Currently Exploring` line itself, since those are NOT known skills.
    """
    if not md:
        return set()

    m = _SKILLS_HEADER_RE.search(md)
    if not m:
        return set()

    section_start = m.end()
    nxt = _NEXT_SECTION_RE.search(md, section_start)
    section_end = nxt.start() if nxt else len(md)
    section = md[section_start:section_end]

    skills: set[str] = set()
    for line in section.splitlines():
        line = line.strip()
        if not re.match(r"^[-*+]\s", line):
            continue
        body = re.sub(r"^[-*+]\s+", "", line)
        if re.match(r"\*\*\s*currently\s+exploring", body, re.IGNORECASE):
            continue
        label_match = _BULLET_LABEL_RE.match(body)
        content = label_match.group(1) if label_match else body
        for item in re.split(r"[,;/|]| and ", content):
            cleaned = item.strip().strip("*_`").strip()
            cleaned = re.sub(r"\s*\([^)]*\)\s*$", "", cleaned)
            if cleaned and 1 < len(cleaned) < 50:
                skills.add(cleaned)
    return skills


def _filter_currently_exploring(
    lines: list[str], master_resume: str
) -> tuple[list[str], list[str]]:
    """Filter implicit prerequisites out of the Currently Exploring bullet.

    Considers skills from BOTH the tailored output AND the master resume as
    "known", so even skills the model dropped from the tailored version still
    count when deciding what's a real gap.

    Returns (new_lines, list_of_removed_items).
    """
    tailored_skills = extract_known_skills("\n".join(lines))
    master_skills = extract_known_skills(master_resume) if master_resume else set()
    known = tailored_skills | master_skills

    if not known:
        return lines, []

    removed_total: list[str] = []
    new_lines: list[str] = []
    for line in lines:
        m = _EXPLORING_RE.match(line)
        if not m:
            new_lines.append(line)
            continue
        prefix, items_str = m.group(1), m.group(2)
        items = [s.strip() for s in re.split(r",\s*", items_str) if s.strip()]
        kept, removed = filter_exploring_items(items, known)
        removed_total.extend(removed)
        # Enforce the hard cap. The model is instructed to obey this in the
        # prompt, but we keep only the first MAX_EXPLORING_ITEMS items
        # (the prompt requires the model to order by JD-importance, so the
        # earliest items are the most critical).
        if len(kept) > MAX_EXPLORING_ITEMS:
            removed_total.extend(kept[MAX_EXPLORING_ITEMS:])
            kept = kept[:MAX_EXPLORING_ITEMS]
        if not kept:
            # Everything was implicit — drop the whole line.
            continue
        new_lines.append(f"{prefix}{', '.join(kept)}")
    return new_lines, removed_total


def _drop_empty_sections(lines: list[str]) -> list[str]:
    out: list[str] = []
    i = 0
    while i < len(lines):
        line = lines[i]
        m = re.match(r"^(#{2,3})\s+(.+)$", line)
        if not m:
            out.append(line)
            i += 1
            continue
        # Collect content until next header at same or higher level.
        section_level = len(m.group(1))
        j = i + 1
        while j < len(lines):
            nxt = re.match(r"^(#{1,6})\s+", lines[j])
            if nxt and len(nxt.group(1)) <= section_level:
                break
            j += 1
        body = [ln for ln in lines[i + 1: j] if ln.strip()]
        if not body:
            i = j
            continue
        out.append(line)
        out.extend(lines[i + 1: j])
        i = j
    return out
