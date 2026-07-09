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
    restored_project_links: list[str] = field(default_factory=list)


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

    tailored_resume, removed_implicit, restored_links = _clean_tailored_resume(
        tailored_resume, master_resume=master_resume,
    )

    return TailoredOutput(
        raw=raw,
        match_analysis=match_analysis,
        tailored_resume=tailored_resume,
        removed_implicit=removed_implicit,
        restored_project_links=restored_links,
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
) -> tuple[str, list[str], list[str]]:
    """Post-process the model's resume markdown to remove common slip-ups:

      - lines that are pure placeholder text ([Include ...], [TBD], etc.)
      - empty sections (header followed by no real content or only a placeholder)
      - trailing meta-commentary paragraphs about the resume itself
      - "(if available)" / "(only if)" parenthetical notes
      - implicit prerequisites in the Currently Exploring line
        (e.g. don't list HTML if the candidate has React)
      - missing project links (spliced back from the master resume)

    Returns (cleaned_markdown, removed_implicit_skills, restored_project_links).
    """
    if not md:
        return md, [], []

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

    # Normalize project blocks to inline header format AND splice in any
    # project the model dropped (including its bullets, from master).
    pruned, restored_links = _normalize_and_restore_projects(pruned, master_resume)

    # Ensure Experience/Projects bullets end with terminal punctuation so no
    # sentence trails off (the model occasionally forgets a period, and
    # rule 16/17 mandate one).
    pruned = _enforce_bullet_terminators(pruned)

    # Remove empty sections: a `##`/`###` header whose body has no real content
    # before the next header.
    pruned = _drop_empty_sections(pruned)

    return "\n".join(pruned).strip(), removed_implicit, restored_links


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


_TERMINAL_PUNCT = (".", "!", "?", ":")
_META_BULLET_RE = re.compile(
    r"^\s*[-*+]\s+\*\*\s*(?:Stack|Link|Demo|URL|Live|Repo|GitHub|Timeline|"
    r"Currently\s+Exploring)\s*:?\s*\*\*",
    re.IGNORECASE,
)
_TERMINATE_SECTIONS = (
    "experience", "work experience", "professional experience", "employment",
    "projects", "project", "personal projects", "selected projects",
    "achievements", "awards", "achievements & awards", "honors & awards",
    "achievements and awards", "leadership", "activities", "volunteer",
)


def _enforce_bullet_terminators(lines: list[str]) -> list[str]:
    """Within Experience/Projects/Achievements sections, ensure every prose
    bullet ends with a period. Skips meta-bullets (**Stack:**, **Link:**,
    **Currently Exploring:**, etc.) since those are structured labels, not
    sentences.

    Runs after project-normalization so **Link:**/**Timeline:** bullets have
    already been folded into the header line.
    """
    out: list[str] = []
    in_target_section = False
    for line in lines:
        m = re.match(r"^##\s+(.+?)\s*$", line)
        if m:
            title = re.sub(r"[^a-z &]", "", m.group(1).lower()).strip()
            in_target_section = title in _TERMINATE_SECTIONS
            out.append(line)
            continue
        if not in_target_section:
            out.append(line)
            continue
        stripped = line.strip()
        if not stripped or not re.match(r"^[-*+]\s+", stripped):
            out.append(line)
            continue
        if _META_BULLET_RE.match(stripped):
            out.append(line)
            continue
        # Strip any trailing markdown emphasis markers so we can inspect the
        # true last character.
        tail = stripped.rstrip()
        while tail and tail[-1] in "*_`":
            tail = tail[:-1].rstrip()
        if not tail:
            out.append(line)
            continue
        if tail[-1] in _TERMINAL_PUNCT:
            out.append(line)
            continue
        # Preserve original trailing decorations and just append the period
        # BEFORE any trailing emphasis markers if they exist.
        trailing_markers = ""
        j = len(line) - 1
        while j >= 0 and line[j] in " \t":
            j -= 1
        while j >= 0 and line[j] in "*_`":
            trailing_markers = line[j] + trailing_markers
            j -= 1
        core = line[: j + 1]
        out.append(f"{core}.{trailing_markers}")
    return out


_PROJECTS_SECTION_RE = re.compile(
    r"^##\s+Projects?\s*$", re.IGNORECASE | re.MULTILINE,
)
_NEXT_H2_RE = re.compile(r"^##\s+", re.MULTILINE)
_URL_RE = re.compile(r"https?://[^\s)]+")
_PROJECT_LINK_BULLET_RE = re.compile(
    r"^\s*[-*+]\s+\*\*\s*(?P<label>Link|Demo|URL|Live|Repo|GitHub)\s*:?\s*\*\*"
    r"\s*:?\s*(?P<rest>.+)$",
    re.IGNORECASE,
)
_PROJECT_TIMELINE_BULLET_RE = re.compile(
    r"^\s*[-*+]\s+\*\*\s*Timeline\s*:?\s*\*\*\s*:?\s*(?P<rest>.+)$",
    re.IGNORECASE,
)
_BARE_URL_BULLET_RE = re.compile(r"^\s*[-*+]\s+(https?://\S+)\s*$")
_INLINE_MD_LINK_RE = re.compile(r"^\[([^\]]+)\]\(([^)]+)\)$")


def _normalize_project_name(name: str) -> str:
    """Collapse a project title to a key suitable for fuzzy matching.

    Strips punctuation, version suffixes, and post-em-dash content so
    `### Talent-Agent — AI Recruiter` still matches master's `### Talent-Agent`.
    Also drops anything after the first `|` so an already-formatted header
    (`### Talent-Agent | [Link](url) | timeline`) normalizes to its title.
    """
    name = name.split("|", 1)[0]
    name = re.split(r"\s+[—–-]\s+", name, maxsplit=1)[0]
    return re.sub(r"[^a-z0-9]", "", name.lower())


def _parse_inline_project_header(text: str) -> dict | None:
    """Parse `Title | [Link](url) | [GitHub](url) | Timeline` into parts.

    A project header must contain at least one `|`. All `[label](url)` chunks
    are collected as links in encounter order; any non-link chunk after the
    title becomes the timeline. Returns None if the text isn't shaped like a
    project header.
    """
    if "|" not in text:
        return None
    parts = [p.strip() for p in text.split("|") if p.strip()]
    if len(parts) < 2:
        return None
    title = parts[0]
    links: list[dict] = []
    timeline: str | None = None
    for p in parts[1:]:
        m = _INLINE_MD_LINK_RE.match(p)
        if m:
            links.append({"label": m.group(1).strip(), "url": m.group(2).strip()})
            continue
        if timeline is None:
            timeline = p
    return {"title": title, "links": links, "timeline": timeline}


def _canonical_link_label(label: str) -> str:
    """Normalize a link label into the canonical form we render.

    Master resumes commonly say Link, Demo, URL, Live, Repo, GitHub. We keep
    `GitHub` as-is (case-sensitive) and coalesce Link/Demo/URL/Live into
    `Link` since they all mean "the live/demo URL" to a reader.
    """
    lo = label.strip().lower()
    if lo in {"github", "repo"}:
        return "GitHub"
    return "Link"


def _link_key(url: str) -> str:
    """Compare-friendly form of a URL for de-duplication."""
    return url.strip().rstrip("/").lower()


def _build_inline_project_header(
    title: str, links: list[dict], timeline: str | None,
) -> str:
    parts: list[str] = [title.strip()]
    seen: set[str] = set()
    for link in links or []:
        url = (link.get("url") or "").strip()
        if not url:
            continue
        key = _link_key(url)
        if key in seen:
            continue
        seen.add(key)
        label = _canonical_link_label(link.get("label") or "Link")
        parts.append(f"[{label}]({url})")
    if timeline:
        parts.append(timeline.strip())
    return "### " + " | ".join(parts)


def _strip_url_extras(url: str) -> str:
    return url.strip().rstrip(".,;)")


def _iter_master_project_chunks(master_md: str):
    """Yield (name, body_text) for each `### Project` block in master."""
    if not master_md:
        return
    m = _PROJECTS_SECTION_RE.search(master_md)
    if not m:
        return
    start = m.end()
    nxt = _NEXT_H2_RE.search(master_md, start)
    section = master_md[start: nxt.start() if nxt else len(master_md)]
    for chunk in re.split(r"(?m)^###\s+", section)[1:]:
        first_nl = chunk.find("\n")
        if first_nl < 0:
            yield chunk.strip(), ""
        else:
            yield chunk[:first_nl].strip(), chunk[first_nl + 1:]


def _extract_master_project_meta(master_md: str) -> dict[str, dict]:
    """Map normalized-name -> {name, links, timeline} for every project in master.

    `links` is a list of {label, url} dicts collected in order, deduped by URL.
    A project can therefore expose BOTH a live demo link and a GitHub repo.
    """
    out: dict[str, dict] = {}
    for raw_name, body in _iter_master_project_chunks(master_md):
        key = _normalize_project_name(raw_name)
        if not key:
            continue
        meta: dict = {
            "name": raw_name.split("|")[0].strip(),
            "links": [],
            "timeline": None,
        }
        seen_urls: set[str] = set()

        def add_link(label: str, url: str) -> None:
            url = _strip_url_extras(url)
            if not url:
                return
            k = _link_key(url)
            if k in seen_urls:
                return
            seen_urls.add(k)
            meta["links"].append({
                "label": _canonical_link_label(label),
                "url": url,
            })

        parsed = _parse_inline_project_header(raw_name)
        if parsed:
            meta["name"] = parsed["title"]
            for link in parsed.get("links") or []:
                add_link(link.get("label") or "Link", link.get("url") or "")
            if parsed.get("timeline"):
                meta["timeline"] = parsed["timeline"]

        for line in body.splitlines():
            if re.match(r"^#{1,3}\s+", line):
                break
            if not line.strip():
                continue
            lm = _PROJECT_LINK_BULLET_RE.match(line)
            if lm:
                url_match = _URL_RE.search(lm.group("rest"))
                if url_match:
                    add_link(lm.group("label"), url_match.group(0))
                continue
            tm = _PROJECT_TIMELINE_BULLET_RE.match(line)
            if tm and meta["timeline"] is None:
                meta["timeline"] = tm.group("rest").strip()
                continue
            bm = _BARE_URL_BULLET_RE.match(line)
            if bm:
                add_link("Link", bm.group(1))
        out[key] = meta
    return out


def _extract_master_project_bullets(master_md: str, project_key: str) -> list[str]:
    """Return the achievement bullets for a master project, with Link/Timeline
    meta-bullets stripped (those move into the inline header)."""
    for raw_name, body in _iter_master_project_chunks(master_md):
        if _normalize_project_name(raw_name) != project_key:
            continue
        out: list[str] = []
        for line in body.splitlines():
            if re.match(r"^#{1,3}\s+", line):
                break
            if _PROJECT_LINK_BULLET_RE.match(line):
                continue
            if _PROJECT_TIMELINE_BULLET_RE.match(line):
                continue
            if _BARE_URL_BULLET_RE.match(line):
                continue
            out.append(line)
        # Trim leading/trailing blank lines.
        while out and not out[0].strip():
            out.pop(0)
        while out and not out[-1].strip():
            out.pop()
        return out
    return []


def _normalize_and_restore_projects(
    lines: list[str], master_resume: str,
) -> tuple[list[str], list[str]]:
    """Three-in-one:
      1. Convert every `### Project` block to the compact inline header format
         `### Title | [Link](url) | Timeline`, moving any legacy
         `- **Link:**`, `- **Timeline:**` bullets into the header line.
      2. Inject Link/Timeline from master if the tailored output dropped them.
      3. Splice in any project from master that's entirely missing from the
         tailored output, using master's own bullets.

    Returns (new_lines, list_of_touched_project_names).
    """
    master_meta = _extract_master_project_meta(master_resume)
    if not master_meta:
        return lines, []

    out: list[str] = []
    touched: list[str] = []
    seen_keys: set[str] = set()

    i = 0
    while i < len(lines):
        line = lines[i]
        header = re.match(r"^###\s+(.+?)\s*$", line)
        if not header:
            out.append(line)
            i += 1
            continue

        full_text = header.group(1)
        parsed = _parse_inline_project_header(full_text) or {}
        cur_title = parsed.get("title") or full_text
        cur_links: list[dict] = list(parsed.get("links") or [])
        cur_timeline = parsed.get("timeline")

        key = _normalize_project_name(cur_title)
        master = master_meta.get(key)
        if not master:
            # Not a master project (e.g. Experience entry under ## Experience).
            out.append(line)
            i += 1
            continue
        seen_keys.add(key)

        # Slice body until next ### or ##.
        j = i + 1
        while j < len(lines) and not re.match(r"^#{2,3}\s+", lines[j]):
            j += 1
        body_lines = lines[i + 1: j]

        seen_urls: set[str] = {_link_key(l["url"]) for l in cur_links if l.get("url")}

        def add_link(label: str, url: str) -> None:
            url = _strip_url_extras(url)
            if not url:
                return
            k = _link_key(url)
            if k in seen_urls:
                return
            seen_urls.add(k)
            cur_links.append({
                "label": _canonical_link_label(label),
                "url": url,
            })

        # Strip legacy Link/Timeline bullets; harvest URLs + timeline into
        # the header line.
        kept_body: list[str] = []
        had_legacy_bullet = False
        for bl in body_lines:
            lm = _PROJECT_LINK_BULLET_RE.match(bl)
            if lm:
                had_legacy_bullet = True
                um = _URL_RE.search(lm.group("rest"))
                if um:
                    add_link(lm.group("label"), um.group(0))
                continue
            tm = _PROJECT_TIMELINE_BULLET_RE.match(bl)
            if tm:
                had_legacy_bullet = True
                if cur_timeline is None:
                    cur_timeline = tm.group("rest").strip()
                continue
            bm = _BARE_URL_BULLET_RE.match(bl)
            if bm:
                had_legacy_bullet = True
                add_link("Link", bm.group(1))
                continue
            kept_body.append(bl)

        # Fill from master if a URL/timeline is still missing.
        injected = False
        for m_link in master.get("links") or []:
            url = m_link.get("url") or ""
            if not url or _link_key(url) in seen_urls:
                continue
            add_link(m_link.get("label") or "Link", url)
            injected = True
        if cur_timeline is None and master.get("timeline"):
            cur_timeline = master["timeline"]
            injected = True

        new_header = _build_inline_project_header(cur_title, cur_links, cur_timeline)
        out.append(new_header)
        out.extend(kept_body)

        if injected or had_legacy_bullet or (parsed.get("links") is not None
                                             and not parsed.get("links") and cur_links):
            touched.append(cur_title)

        i = j

    # Splice in any master project that's entirely missing from tailored.
    missing_keys = [k for k in master_meta if k not in seen_keys]
    if missing_keys:
        out_h2_idx = None
        for idx, line in enumerate(out):
            if re.match(r"^##\s+Projects?\s*$", line, re.IGNORECASE):
                out_h2_idx = idx
                break
        if out_h2_idx is not None:
            end_idx = len(out)
            for idx in range(out_h2_idx + 1, len(out)):
                if re.match(r"^##\s+", out[idx]):
                    end_idx = idx
                    break
            # Strip any trailing blank line just before end_idx for clean splice.
            insert_at = end_idx
            while insert_at > out_h2_idx + 1 and not out[insert_at - 1].strip():
                insert_at -= 1
            splice: list[str] = []
            for key in missing_keys:
                m = master_meta[key]
                splice.append("")
                splice.append(_build_inline_project_header(
                    m["name"], m.get("links") or [], m.get("timeline"),
                ))
                splice.extend(_extract_master_project_bullets(master_resume, key))
                touched.append(m["name"])
            out = out[:insert_at] + splice + out[insert_at:]

    return out, touched


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
