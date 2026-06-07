# Resume Tailor

An ATS-optimized resume tailoring desktop app powered by **Anthropic Claude** or **OpenAI GPT**, built with **Streamlit**.

Drop in your master resume + a job description, get back:

1. A **Match Analysis** (match score, matched skills, missing skills, gaps).
2. A **Tailored Resume** in markdown, downloadable as a **dense single-page PDF** that mirrors your original resume's font and accent color.

The model is hard-constrained to never invent experience that isn't already in your master resume. Gap skills from the JD are surfaced honestly in a clearly-labelled `**Currently Exploring:**` line — never as fake experience.

---

## Features

### Tailoring

- **Choose your LLM** — Anthropic Claude or OpenAI GPT (toggle in sidebar)
- **Master resume + JD** in a two-pane UI
- **PDF / Markdown / Text upload** for both inputs (text is auto-extracted from PDFs)
- **Local master resume** — `master_resume.md` auto-loads on startup
  - Edit inline and click **Save to disk**, or **Reload from disk**
- **Live streaming output** from the chosen model
- **Tabbed view**: Tailored Resume / Match Analysis / Raw Output
- **Defensive UI warnings** — flags if the model dropped your sections or skipped the gaps line

### Output

- **Single-page PDF** guaranteed
  - Auto-**shrinks** if content overflows (down to 60% scale)
  - Auto-**grows** if content is short, filling the page (up to 150% scale)
- **Style mirroring** — when you upload your resume PDF, the app detects its
  dominant font family (serif / sans-serif / mono) and accent color, and uses
  them in the output PDF
- **Manual style override** — sidebar font + color picker
- **Markdown download** — for editing in any tool

### Safety / truthfulness

- Hard rule: **never invent** experience, skills, projects, or metrics
- Truthful JD-vocabulary aliasing (e.g. Kafka + K8s → "distributed systems")
- Mandatory `Currently Exploring:` line for honest gap signalling
- Post-processor strips placeholder text (`[TBD]`, `[Add ...]`) and trailing meta-commentary
- Section-preservation check warns if any section from your master resume was dropped

---

## Quick start

### Windows (PowerShell / CMD)

Easiest: **double-click `run.bat`** (or run `.\run.ps1`).

It creates a `.venv`, installs dependencies, and launches the app.

### Linux / WSL / macOS

```bash
chmod +x run.sh
./run.sh
```

### Manual setup (any OS)

```bash
# Windows PowerShell:
python -m venv .venv
.\.venv\Scripts\Activate.ps1

# Linux / WSL / macOS:
python3 -m venv .venv
source .venv/bin/activate

pip install -r requirements.txt

# Optional: store your API key persistently
cp .env.example .env   # then edit it

streamlit run app.py
```

The app opens at <http://localhost:8501>.

> If you see `streamlit: command not found`, your venv is active but the deps
> aren't installed — run `pip install -r requirements.txt`.

---

## Getting an API key

You only need **one** provider's key (use whichever you have credits with).

### Anthropic (Claude)

1. Create a key at <https://console.anthropic.com/settings/keys>
2. Check credits at <https://console.anthropic.com/settings/billing>
3. Format: `sk-ant-api03-...`

### OpenAI (GPT)

1. Create a key at <https://platform.openai.com/api-keys>
2. Check credits at <https://platform.openai.com/account/billing/overview>
3. Format: `sk-...`

### Where to put the key

Either:

- Paste it into the **sidebar** of the running app (per-session), **or**
- Put it in a `.env` file in the project root:

```env
ANTHROPIC_API_KEY=sk-ant-...
OPENAI_API_KEY=sk-...
```

Fill in one or both — the app uses whichever provider is selected in the sidebar. The unused key is ignored.

---

## Usage — the typical flow

1. **First-time setup**: open `master_resume.md` and fill it with **everything** you've ever done (jobs, projects, skills, achievements, certifications). The richer this file, the better every future tailoring.
2. **Per job application**:
   - Paste / upload the JD on the right panel
   - (Optional) upload your existing resume PDF — the app will mirror its font + accent color in the output
   - Click **Tailor Resume**
   - Switch to the **Tailored Resume** tab
   - Click **Download PDF**
3. The PDF is one page, styled to match your original, with keywords aligned to the JD wherever truthful.

---

## Project layout

```
resume-tailor/
├── app.py              # Streamlit UI + safety checks
├── tailor.py           # Provider-agnostic LLM client + response parser + post-processor
├── prompt.py           # System + user prompt templates
├── pdf_export.py       # reportlab-based PDF renderer (single-page guaranteed)
├── pdf_import.py       # PDF -> plain text (pypdf)
├── style_extract.py    # Extract font family + accent color from uploaded PDFs (pymupdf)
├── master_resume.md    # Your local master resume (auto-loaded, gitignored)
├── requirements.txt
├── run.bat             # Windows double-click launcher
├── run.ps1             # PowerShell launcher
├── run.sh              # Linux / WSL / macOS launcher
├── .env.example
└── README.md
```

---

## How it works

```
[Your PDF resume] + [Job Description]
            ↓
    Streamlit UI (app.py)
            ↓
   Extract text from PDF (pdf_import.py)
   Extract style hints from PDF (style_extract.py)
            ↓
       Anthropic Claude or OpenAI GPT
       (provider-agnostic in tailor.py)
       constrained by strict prompt (prompt.py)
            ↓
   Stream markdown response
            ↓
   Parse → Match Analysis + Tailored Resume
   Post-process: strip placeholders, empty sections, meta-commentary
            ↓
   Render to PDF (pdf_export.py via reportlab)
   Auto-scale to fill exactly one page
            ↓
   Single-page tailored PDF download
```

### Key design choices

| | Why |
|---|---|
| **reportlab** for PDF | Battle-tested, single-page via auto-scale loop (no clipping bugs) |
| **pymupdf** for style extraction | Fast, accurate at pulling fonts and colors from existing PDFs |
| **markdown intermediate** | LLMs are best at markdown; easy to inspect / edit / re-render |
| **streaming responses** | See the model thinking in real time |
| **per-provider env vars** | One key per provider, swap freely without restart |
| **post-processor in tailor.py** | Defense-in-depth — even if the prompt fails, output stays clean |

---

## Tips for best results

- **Fill `master_resume.md` as completely as possible**. Every "missing keyword"
  in your Match Analysis is usually a master-resume gap, not a gap in your
  actual experience. Tools you used but didn't list (Postman, Pytest, Linear,
  Helm, OAuth2, etc.) → add them once, benefit forever.
- **Paste the complete JD** — responsibilities, required skills, and nice-to-haves. More signal = better tailoring.
- **Upload your existing PDF resume** to get style mirroring (font + accent color).
- **Read the Match Analysis tab** after each tailoring — `Potential Gaps`
  tells you what the JD asks for that your resume doesn't cover. Use this as
  a learning roadmap, not just a tailoring artifact.
- **Trust the warnings** — if the UI flags a missing section or missing gaps
  line, click **Tailor Resume** again to regenerate.

---

## How truthfulness is enforced

Three layers:

1. **System prompt** — explicit hard rules forbidding invented experience, placeholder text, and meta-commentary.
2. **Truthful aliasing only** — the JD's vocabulary may be used in bullets *only if* it maps to real experience in your master.
3. **Post-processor** (`tailor.py::_clean_tailored_resume`) — strips `[Include ...]`, `[TBD]`, parenthetical hints, empty sections, and trailing prose if the model slips up.

The **only** place a never-used technology may appear is the
`**Currently Exploring:**` line, where the label makes the meaning unambiguous
to recruiters: aware of it, working on it, no production experience yet.

---

## Troubleshooting

- **`invalid x-api-key` (401)** — the key is wrong, has whitespace, or the
  account has no credits. Create a fresh one and check billing.
- **`streamlit: command not found`** — venv is active but deps aren't installed.
  Run `pip install -r requirements.txt`.
- **PDF is missing a section that's in your master** — yellow warning will
  appear at the top of the resume tab. Click **Tailor Resume** again.
- **PDF says "Content was trimmed to page 1"** — even at 60% scale your
  content needed 2 pages. Trim your master, or ask the model to be terser by
  removing some lower-priority items.
- **No text extracted from uploaded PDF** — your PDF is likely scanned/image-only.
  Run it through OCR first (Tesseract, ABBYY, Adobe) and re-upload.
- **`Not enough horizontal space` errors** — should be impossible now with
  reportlab; if you see one, share the input markdown and I'll patch the renderer.

---

## Roadmap ideas

- CLI tool (`resume-tailor --jd job.txt --out tailored.pdf`)
- Browser extension that scrapes JD from job posting pages
- DOCX export
- Cover letter generation from the same master resume
- Multi-profile support (e.g. `master_resume.backend.md` + `master_resume.ml.md`)
- OCR pre-step for scanned PDFs

---

## License

MIT — do whatever you want with it.
