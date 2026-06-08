"""Resume Tailor - Streamlit desktop app.

Run with:
    streamlit run app.py
"""

from __future__ import annotations

import os
import re
from datetime import datetime
from pathlib import Path

import streamlit as st
from dotenv import load_dotenv

from pdf_export import render_pdf
from pdf_import import extract_text_from_pdf
from style_extract import StyleHints, extract_style_hints
from tailor import (
    AVAILABLE_MODELS,
    DEFAULT_MAX_TOKENS,
    DEFAULT_MODELS,
    ENV_KEYS,
    parse_output,
    stream_tailored_resume,
)


load_dotenv()


APP_DIR = Path(__file__).resolve().parent
MASTER_RESUME_PATH = APP_DIR / "master_resume.md"


PROVIDER_LABELS = {
    "anthropic": "Anthropic (Claude)",
    "openai": "OpenAI (GPT)",
}
PROVIDER_KEY_PREFIX = {
    "anthropic": "sk-ant-",
    "openai": "sk-",
}


def load_local_master_resume() -> str:
    if MASTER_RESUME_PATH.exists():
        try:
            return MASTER_RESUME_PATH.read_text(encoding="utf-8")
        except OSError:
            return ""
    return ""


def save_local_master_resume(content: str) -> None:
    MASTER_RESUME_PATH.write_text(content, encoding="utf-8")


def read_uploaded_file(uploaded) -> tuple[str, bytes | None]:
    """Read an uploaded file (.pdf/.md/.txt/.markdown).

    Returns (text, raw_pdf_bytes). raw_pdf_bytes is non-None only for PDFs and
    is used for visual style extraction.
    """
    name = uploaded.name.lower()
    data = uploaded.read()
    if name.endswith(".pdf"):
        return extract_text_from_pdf(data), data
    try:
        return data.decode("utf-8"), None
    except UnicodeDecodeError:
        return data.decode("latin-1", errors="ignore"), None


def init_state() -> None:
    st.session_state.setdefault("raw_output", "")
    st.session_state.setdefault("match_analysis", "")
    st.session_state.setdefault("tailored_resume", "")
    st.session_state.setdefault("generated_at", None)
    st.session_state.setdefault("master_resume", load_local_master_resume())
    st.session_state.setdefault("job_description", "")
    st.session_state.setdefault("template_style", None)
    st.session_state.setdefault("template_pdf_name", None)
    st.session_state.setdefault("removed_implicit", [])
    st.session_state.setdefault("restored_project_links", [])


def render_sidebar() -> dict:
    st.sidebar.header("Settings")

    provider = st.sidebar.radio(
        "Provider",
        options=list(PROVIDER_LABELS.keys()),
        format_func=lambda p: PROVIDER_LABELS[p],
        horizontal=True,
        index=0,
    )

    env_key_name = ENV_KEYS[provider]
    api_key = st.sidebar.text_input(
        f"{PROVIDER_LABELS[provider]} API Key",
        value=os.getenv(env_key_name, ""),
        type="password",
        key=f"api_key_{provider}",
        help=(
            f"Reads `{env_key_name}` from .env on startup. "
            "Stored only in this session."
        ),
    )
    api_key = (api_key or "").strip()

    expected_prefix = PROVIDER_KEY_PREFIX[provider]
    if api_key and not api_key.startswith(expected_prefix):
        st.sidebar.warning(
            f"That doesn't look like a {PROVIDER_LABELS[provider]} key — "
            f"they start with `{expected_prefix}`."
        )

    models = AVAILABLE_MODELS[provider]
    default_model = DEFAULT_MODELS[provider]
    default_idx = models.index(default_model) if default_model in models else 0
    model = st.sidebar.selectbox(
        "Model", models, index=default_idx, key=f"model_{provider}"
    )

    max_tokens = st.sidebar.slider(
        "Max output tokens", min_value=1024, max_value=8192,
        value=DEFAULT_MAX_TOKENS, step=256,
    )

    st.sidebar.divider()
    st.sidebar.subheader("Tips")
    st.sidebar.markdown(
        "- Paste your **full** master resume (everything you've ever done).\n"
        "- Paste the **complete** job description.\n"
        "- The model will only use facts already in your resume.\n"
        "- Use the PDF download to send directly to recruiters."
    )

    return {
        "provider": provider,
        "api_key": api_key or None,
        "model": model,
        "max_tokens": max_tokens,
    }


def render_inputs() -> tuple[str, str]:
    col1, col2 = st.columns(2)

    with col1:
        header_l, header_r = st.columns([3, 2])
        with header_l:
            st.subheader("Master Resume")
        with header_r:
            st.caption(f"Local file: `{MASTER_RESUME_PATH.name}`")

        uploaded = st.file_uploader(
            "Upload resume (PDF / MD / TXT)",
            type=["pdf", "md", "txt", "markdown"],
            key="master_resume_upload",
        )
        if uploaded is not None:
            sig = (uploaded.name, uploaded.size)
            if st.session_state.get("_last_resume_sig") != sig:
                try:
                    text, pdf_bytes = read_uploaded_file(uploaded)
                    if not text.strip():
                        st.warning(
                            f"No text extracted from {uploaded.name}. "
                            "If it's a scanned PDF you'll need an OCR step first."
                        )
                    else:
                        st.session_state.master_resume = text
                        st.session_state["_last_resume_sig"] = sig
                        if pdf_bytes:
                            try:
                                style = extract_style_hints(pdf_bytes)
                                st.session_state.template_style = style
                                st.session_state.template_pdf_name = uploaded.name
                            except Exception:
                                pass
                        st.toast(f"Loaded {uploaded.name}", icon="✅")
                        st.rerun()
                except Exception as exc:
                    st.error(f"Could not read {uploaded.name}: {exc}")

        master = st.text_area(
            "Paste your full master resume (markdown or plain text)",
            value=st.session_state.master_resume,
            height=380,
            key="master_resume_input",
            placeholder="# John Doe\nSoftware Engineer\n\n## Experience\n...",
            label_visibility="collapsed",
        )

        btn_save, btn_reload, _ = st.columns([1, 1, 2])
        with btn_save:
            if st.button("Save to disk", use_container_width=True,
                         help=f"Write to {MASTER_RESUME_PATH}"):
                try:
                    save_local_master_resume(master)
                    st.session_state.master_resume = master
                    st.toast("Master resume saved", icon="💾")
                except OSError as exc:
                    st.error(f"Save failed: {exc}")
        with btn_reload:
            if st.button("Reload from disk", use_container_width=True):
                st.session_state.master_resume = load_local_master_resume()
                st.session_state.pop("_last_resume_sig", None)
                st.rerun()

    with col2:
        st.subheader("Job Description")
        st.caption("Paste the full posting — or upload a PDF / TXT.")

        jd_upload = st.file_uploader(
            "Upload job description (PDF / MD / TXT)",
            type=["pdf", "md", "txt", "markdown"],
            key="jd_upload",
        )
        if jd_upload is not None:
            sig = (jd_upload.name, jd_upload.size)
            if st.session_state.get("_last_jd_sig") != sig:
                try:
                    text, _ = read_uploaded_file(jd_upload)
                    if not text.strip():
                        st.warning(f"No text extracted from {jd_upload.name}.")
                    else:
                        st.session_state.job_description = text
                        st.session_state["_last_jd_sig"] = sig
                        st.toast(f"Loaded {jd_upload.name}", icon="✅")
                        st.rerun()
                except Exception as exc:
                    st.error(f"Could not read {jd_upload.name}: {exc}")

        jd = st.text_area(
            "Job description",
            value=st.session_state.job_description,
            height=380,
            key="job_description_input",
            placeholder="Senior Backend Engineer at Acme Corp...",
            label_visibility="collapsed",
        )
    return master, jd


def safe_filename(name: str) -> str:
    name = re.sub(r"[^A-Za-z0-9._-]+", "_", name).strip("_")
    return name or "tailored_resume"


def derive_candidate_name(resume_md: str) -> str:
    """Pull a name from the first markdown heading or first non-empty line."""
    for line in resume_md.splitlines():
        s = line.strip().lstrip("#").strip()
        if s:
            return s.split("|")[0].split("-")[0].strip()
    return "Candidate"


def render_outputs() -> None:
    if not st.session_state.raw_output:
        st.info("Fill in both inputs and click **Tailor Resume** to generate.")
        return

    tab_resume, tab_match, tab_raw = st.tabs(
        ["Tailored Resume", "Match Analysis", "Raw Output"]
    )

    with tab_resume:
        if st.session_state.tailored_resume:
            _check_gaps_present()
            _show_filtered_implicit_skills()
            _show_restored_project_links()
            st.markdown(st.session_state.tailored_resume)
            st.divider()
            _render_downloads()
        else:
            st.warning("Couldn't parse a Tailored Resume section from the model output.")

    with tab_match:
        if st.session_state.match_analysis:
            st.markdown(st.session_state.match_analysis)
        else:
            st.warning("Couldn't parse a Match Analysis section from the model output.")

    with tab_raw:
        st.code(st.session_state.raw_output, language="markdown")


def _render_downloads() -> None:
    md_text = st.session_state.tailored_resume
    candidate = safe_filename(derive_candidate_name(md_text))
    stamp = (st.session_state.generated_at or datetime.now()).strftime("%Y%m%d_%H%M")
    base = f"{candidate}_resume_{stamp}"

    opt_col1, opt_col2 = st.columns([1, 2])
    with opt_col1:
        single_page = st.toggle(
            "Force single-page PDF",
            value=True,
            help=(
                "When on, the layout is shrunk-to-fit so the PDF is always "
                "exactly one Letter page."
            ),
        )
    with opt_col2:
        style = _resolve_active_style()

    col1, col2 = st.columns(2)
    with col1:
        st.download_button(
            label="Download Markdown",
            data=md_text.encode("utf-8"),
            file_name=f"{base}.md",
            mime="text/markdown",
            use_container_width=True,
        )
    with col2:
        try:
            result = render_pdf(md_text, style=style, force_single_page=single_page)
            st.download_button(
                label="Download PDF",
                data=result["pdf"],
                file_name=f"{base}.pdf",
                mime="application/pdf",
                use_container_width=True,
                type="primary",
            )
            if single_page and result["clipped"]:
                st.warning(
                    f"Content overflowed at full size (natural length: {result['pages']} pages). "
                    "Output was trimmed to page 1 — consider trimming your master resume "
                    "or asking the model to be more concise."
                )
            elif single_page:
                st.caption("Single-page PDF generated.")
        except Exception as exc:
            st.error(f"PDF generation failed: {exc}")


def _check_gaps_present() -> None:
    """If the Match Analysis flagged gaps but the resume omitted the
    Currently Exploring line, warn the user (the model dropped instructions)."""
    resume = st.session_state.tailored_resume or ""
    match = st.session_state.match_analysis or ""

    gaps_in_match = bool(re.search(
        r"(potential gaps|missing skills)[\s\S]{0,400}?[-*+]\s+\S",
        match,
        re.IGNORECASE,
    ))
    explore_in_resume = "currently exploring" in resume.lower()

    if gaps_in_match and not explore_in_resume:
        st.warning(
            "The model identified gaps in the Match Analysis but did not include "
            "a **Currently Exploring** line in the tailored resume. "
            "Click **Tailor Resume** again to regenerate."
        )

    _check_sections_present()


def _show_filtered_implicit_skills() -> None:
    """If we stripped implicit-prerequisite skills from Currently Exploring,
    surface what was removed so the user has full transparency."""
    removed = st.session_state.get("removed_implicit") or []
    if not removed:
        return
    with st.expander(
        f"Filtered {len(removed)} implicit skill(s) from Currently Exploring",
        expanded=False,
    ):
        st.caption(
            "These were removed because your existing skills already imply "
            "competence (e.g. React implies HTML/CSS/JavaScript). Listing them "
            "would look naive to a recruiter."
        )
        st.markdown(", ".join(f"`{s}`" for s in removed))


def _show_restored_project_links() -> None:
    """If the model dropped a project (or its link/timeline), tell the user
    that the post-processor spliced it back in from the master."""
    restored = st.session_state.get("restored_project_links") or []
    if not restored:
        return
    st.info(
        "Restored project content the model dropped or reformatted: "
        + ", ".join(f"**{name}**" for name in restored)
        + ". Source: master resume."
    )


_SECTION_ALIASES = {
    "summary": {"summary", "profile", "objective", "about"},
    "skills": {"skills", "technical skills", "technologies", "tech stack"},
    "experience": {"experience", "work experience", "professional experience", "employment"},
    "projects": {"projects", "personal projects", "selected projects"},
    "education": {"education", "academic background"},
    "achievements": {"achievements", "awards", "honors", "achievements & awards",
                     "honors & awards", "achievements and awards"},
    "certifications": {"certifications", "certificates", "courses"},
    "publications": {"publications", "research"},
    "leadership": {"leadership", "activities", "volunteer", "volunteering"},
    "languages": {"languages"},
}


def _extract_sections(md: str) -> set[str]:
    """Return a normalized set of section keys present in a markdown resume."""
    found: set[str] = set()
    for line in md.splitlines():
        m = re.match(r"^#{1,3}\s+(.+?)\s*$", line)
        if not m:
            continue
        title = re.sub(r"[^a-z &]", "", m.group(1).lower()).strip()
        for key, aliases in _SECTION_ALIASES.items():
            if title in aliases:
                found.add(key)
                break
    return found


def _check_sections_present() -> None:
    """Warn if any section from the master resume is missing in the tailored output."""
    master = st.session_state.master_resume or ""
    tailored = st.session_state.tailored_resume or ""
    if not master or not tailored:
        return

    master_sections = _extract_sections(master)
    tailored_sections = _extract_sections(tailored)
    missing = master_sections - tailored_sections

    if missing:
        nice = ", ".join(sorted(s.title() for s in missing))
        st.warning(
            f"The following sections from your master resume were not included "
            f"in the tailored output: **{nice}**. "
            "Click **Tailor Resume** again to regenerate — the prompt now asks "
            "the model to preserve every section."
        )


def _resolve_active_style() -> StyleHints:
    """Decide which StyleHints to render with, based on session state + manual override."""
    detected: StyleHints | None = st.session_state.get("template_style")
    template_name = st.session_state.get("template_pdf_name")

    if detected and detected.detected:
        label = (
            f"Template style detected from **{template_name}** "
            f"(font: {detected.font_family}, accent: `{detected.accent_hex}`)"
        )
        st.caption(label)
        if st.toggle("Override template style", value=False, key="style_override"):
            return _manual_style_picker(detected)
        return detected

    st.caption("No template PDF uploaded — using default style. Upload a PDF resume to mirror its look.")
    if st.toggle("Customize style", value=False, key="style_customize"):
        return _manual_style_picker(StyleHints())
    return StyleHints()


def _manual_style_picker(base: StyleHints) -> StyleHints:
    fonts = {
        "Helvetica (sans-serif)": "Helvetica",
        "Times-Roman (serif)": "Times-Roman",
        "Courier (mono)": "Courier",
    }
    rev = {v: k for k, v in fonts.items()}
    default_label = rev.get(base.font_family, "Helvetica (sans-serif)")
    font_label = st.selectbox(
        "Font family",
        list(fonts.keys()),
        index=list(fonts.keys()).index(default_label),
        key="style_font_choice",
    )
    accent = st.color_picker("Accent color", value=base.accent_hex, key="style_accent_choice")
    return StyleHints(
        font_family=fonts[font_label],
        is_serif=fonts[font_label] == "Times-Roman",
        accent_hex=accent,
        section_order=base.section_order,
        detected=base.detected,
        notes=base.notes,
    )


PROVIDER_HELP = {
    "anthropic": {
        "keys_url": "https://console.anthropic.com/settings/keys",
        "billing_url": "https://console.anthropic.com/settings/billing",
        "fallback_model": "claude-3-5-sonnet-latest",
    },
    "openai": {
        "keys_url": "https://platform.openai.com/api-keys",
        "billing_url": "https://platform.openai.com/account/billing/overview",
        "fallback_model": "gpt-4o-mini",
    },
}


def _render_provider_error(exc: Exception, cfg: dict) -> None:
    msg = str(exc)
    lower = msg.lower()
    provider = cfg["provider"]
    help_info = PROVIDER_HELP[provider]
    label = PROVIDER_LABELS[provider]

    auth_signals = (
        "invalid x-api-key", "authentication_error", "invalid_api_key",
        "incorrect api key", "401",
    )
    credit_signals = ("credit balance", "insufficient_quota", "billing", "quota")
    model_signals = ("not_found_error", "model_not_found", "does not exist", "no access")
    rate_signals = ("rate_limit", "429", "rate-limited")

    if any(s in lower for s in auth_signals):
        st.error(
            f"{label} rejected the API key (401). Most likely causes:\n\n"
            "1. The key has a typo or trailing whitespace — paste it again.\n"
            f"2. The key was revoked or belongs to a different workspace — "
            f"create a fresh one at {help_info['keys_url']}.\n"
            f"3. Your account has no credits — add billing at {help_info['billing_url']}.\n\n"
            f"_Raw error:_ `{msg}`"
        )
    elif any(s in lower for s in credit_signals):
        st.error(
            f"{label} says your account has no credits / hit its quota. "
            f"Add billing at {help_info['billing_url']} and try again.\n\n"
            f"_Raw error:_ `{msg}`"
        )
    elif any(s in lower for s in model_signals):
        st.error(
            f"Model `{cfg['model']}` isn't available on your {label} account. "
            f"Pick a different model in the sidebar (e.g. `{help_info['fallback_model']}`)."
            f"\n\n_Raw error:_ `{msg}`"
        )
    elif any(s in lower for s in rate_signals):
        st.error(
            f"{label} rate-limited the request. Wait a few seconds and retry."
            f"\n\n_Raw error:_ `{msg}`"
        )
    else:
        st.error(f"Generation failed ({label}): {msg}")


def run_generation(master: str, jd: str, cfg: dict) -> None:
    st.session_state.master_resume = master
    st.session_state.job_description = jd

    placeholder = st.empty()
    buffer: list[str] = []
    provider_label = PROVIDER_LABELS[cfg["provider"]]
    try:
        with st.spinner(f"Tailoring your resume with {provider_label}..."):
            for chunk in stream_tailored_resume(
                master_resume=master,
                job_description=jd,
                provider=cfg["provider"],
                api_key=cfg["api_key"],
                model=cfg["model"],
                max_tokens=cfg["max_tokens"],
            ):
                buffer.append(chunk)
                placeholder.markdown(
                    "**Streaming...**\n\n" + "".join(buffer)[-4000:]
                )
    except Exception as exc:
        _render_provider_error(exc, cfg)
        return

    raw = "".join(buffer)
    parsed = parse_output(raw, master_resume=master)
    st.session_state.raw_output = parsed.raw
    st.session_state.match_analysis = parsed.match_analysis
    st.session_state.tailored_resume = parsed.tailored_resume
    # Defensive getattr: Streamlit Cloud's hot-reload sometimes caches an
    # older version of the `tailor` module after a push, so newer fields on
    # TailoredOutput can be missing in `parsed`. Falling back to [] keeps
    # the app functional until the user reboots the app to clear sys.modules.
    st.session_state.removed_implicit = getattr(parsed, "removed_implicit", []) or []
    st.session_state.restored_project_links = (
        getattr(parsed, "restored_project_links", []) or []
    )
    st.session_state.generated_at = datetime.now()
    placeholder.empty()


def main() -> None:
    st.set_page_config(
        page_title="Resume Tailor",
        page_icon="📝",
        layout="wide",
    )
    init_state()

    st.title("Resume Tailor")
    st.caption(
        "ATS-optimized resume tailoring powered by Claude. "
        "Your master resume + a job description -> a targeted, recruiter-ready PDF."
    )

    cfg = render_sidebar()
    master, jd = render_inputs()

    btn_col, _ = st.columns([1, 4])
    with btn_col:
        generate = st.button(
            "Tailor Resume",
            type="primary",
            use_container_width=True,
            disabled=not (master.strip() and jd.strip()),
        )

    if generate:
        if not cfg["api_key"]:
            st.error(
                f"Please provide a {PROVIDER_LABELS[cfg['provider']]} API key "
                "in the sidebar."
            )
        else:
            run_generation(master, jd, cfg)

    st.divider()
    render_outputs()


if __name__ == "__main__":
    main()
