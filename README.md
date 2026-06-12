# resume-tailor

Tailor a LaTeX résumé to a specific job posting and produce a PDF — without ever
breaking your layout. Point it at a job URL; it scrapes the posting, uses a
pluggable LLM to rewrite only the job-specific prose, and compiles a tailored PDF.

```
job URL ──► scrape ──► LLM tailors marked prose ──► splice into .tex ──► compile PDF
                                                  ▲
                                         profile corpus
                                         (extra achievements)
```

The LLM only edits text inside `% <TAILOR:...>` markers; it never touches LaTeX
structure, so your formatting is safe by construction.

> **Design & internals:** see [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) for the
> full design decisions, pipeline stages, LLM provider model, and step-by-step
> guide to adding a new LLM backend.

---

## 1. Install

```bash
git clone https://github.com/Diwaker-Singh/resume-tailor.git
cd resume-tailor
./install.sh
```

The installer is idempotent and sets up everything:
- a Python virtualenv (`.venv`) + dependencies,
- checks for a LaTeX engine (and tells you how to install BasicTeX if missing),
- creates `resume-tailor.toml` from the template (if absent),
- prompts for your free Gemini API key → saves `.env.local` (gitignored),
- installs a `resume-tailor` command into `~/.local/bin` (and adds it to PATH),
- runs the test suite as a smoke check.

Open a new terminal afterwards so the `resume-tailor` command is on your PATH.

> **LaTeX engine:** PDF output needs `pdflatex` (recommended on macOS via
> `brew install --cask basictex`, then
> `sudo /Library/TeX/texbin/tlmgr install fontawesome5 enumitem titlesec parskip`).
> Tectonic works too but currently crashes on `fontawesome5` on recent macOS, so
> the tool prefers `pdflatex`/`latexmk`.

To remove: `./uninstall.sh` (keeps your config, key, and generated resumes).

---

## 2. Run

```bash
resume-tailor "https://job-boards.greenhouse.io/anthropic/jobs/4951844008"
```

That's it — no venv activation, no env loading. The launcher handles all of it
and reads your settings from `resume-tailor.toml`. Output goes to your configured
`output_dir` (a per-job subfolder if enabled).

Override anything per-run with flags:
```bash
resume-tailor "<url>" --resume other.tex --out-dir /tmp --model gemini-2.5-pro --no-pdf
```

### Re-running the same job
Output is written to a deterministic per-job folder. On a re-run, the previous
`.tex`, `.pdf`, and `job.json` are moved to `<name>.bak` (exactly one prior
version is kept) before the new result is written. Pass `--no-backup` to skip
this.

### All CLI flags

| Flag | Default | Description |
|------|---------|-------------|
| `url` (positional) | — | Job posting URL |
| `-r, --resume` | from config | Path to source `.tex` resume |
| `-o, --out-dir` | from config | Output parent directory |
| `--config` | auto-discover | Path to config TOML file |
| `--provider` | from config | LLM provider (`gemini`, `openai`, `ollama`, `echo`) |
| `--model` | provider default | Override LLM model ID |
| `--no-subdir` | off | Disable per-job output subfolder |
| `--no-pdf` | off | Only write `.tex`, skip PDF compilation |
| `--no-backup` | off | Skip keeping a `.bak` of previous run |
| `--allow-overflow` | off | Accept multi-page output |
| `-v, -vv` | silent | Increase log verbosity |

---

## 3. Configuration — `resume-tailor.toml`

Settings live in `resume-tailor.toml` (created by the installer from
`resume-tailor.example.toml`). Precedence: **CLI flag > env var > config file > default**.

```toml
[paths]
resume     = "~/workplace/resume-tailor/examples/sample_resume.tex"
output_dir = "~/Documents/Tailored_Resumes"

[output]
per_job_subdir  = true              # each job gets its own folder
subdir_template = "{company}-{title}"   # placeholders: {company} {title} {date} {stem}

[llm]
provider = "gemini"
model    = ""                       # empty => provider default (free-tier model)

[profile]
context_dir = ""                    # folder of .md/.txt reference corpus
max_chars   = 12000                 # cap on profile context fed to LLM
```

The config file is searched in this order: `--config <path>`,
`$RESUME_TAILOR_CONFIG`, `./resume-tailor.toml`, `~/.config/resume-tailor/config.toml`.
Your API key stays in `.env.local` (never in the config file, never committed).

### Environment variables

| Variable | Overrides |
|----------|-----------|
| `RESUME_TAILOR_DEFAULT_RESUME` | Default `.tex` resume path |
| `RESUME_TAILOR_OUTPUT_DIR` | Output directory |
| `RESUME_TAILOR_PROVIDER` | LLM provider name |
| `RESUME_TAILOR_MODEL` | LLM model ID |
| `RESUME_TAILOR_MAX_COMPACTION_PASSES` | One-page retry limit |
| `RESUME_TAILOR_LOG_LEVEL` | Log verbosity (`DEBUG`, `INFO`, `WARNING`) |
| `GEMINI_API_KEY` | Google Gemini API key |
| `GEMINI_MODEL` | Gemini model override |
| `OPENAI_API_KEY` | OpenAI API key |
| `OLLAMA_HOST` | Ollama server URL (default `http://localhost:11434`) |

---

## 4. The résumé file

Your canonical résumé is a normal LaTeX file with `% <TAILOR:...>` comment
markers around the prose that should adapt per job. Everything outside markers
— header, company names, titles, dates, education, LaTeX structure — is frozen
and never sent to the LLM.

Markers are LaTeX comments, so the résumé renders identically with or without them.

To add a tailorable region:
```latex
% <TAILOR:my_region>
... editable text ...
% </TAILOR>
```

If your résumé has no markers at all, the tool falls back to auto-detecting
`\item` bullet text as editable regions.

For a complete working example, see [`examples/sample_resume.tex`](examples/sample_resume.tex).

---

## 5. Configure the LLM (no code changes)

Default: **Gemini**, model **`gemini-flash-lite-latest`** — the only model that
works reliably on Google's free tier (2.5-pro = `limit:0`; 2.5-flash = 10 req/min).

| To change | How | Example |
|-----------|-----|---------|
| Model (one-off) | `--model` | `--model gemini-2.5-pro` |
| Model (persistent) | `GEMINI_MODEL` | `export GEMINI_MODEL=gemini-2.5-flash` |
| Provider (one-off) | `--provider` | `--provider ollama` |
| Provider (persistent) | `RESUME_TAILOR_PROVIDER` | `gemini \| openai \| ollama \| echo` |

### Providers

| Provider | Use | Setup |
|----------|-----|-------|
| `gemini` | default, free tier | `GEMINI_API_KEY` in `.env.local` |
| `openai` | paid, high quality | `OPENAI_API_KEY` |
| `ollama` | **local, private, no key/quota** | install Ollama, then `ollama serve` + `ollama pull <model>` |
| `echo` | offline tests | none |

### Run fully local & private with Ollama
Your résumé/profile data never leaves your machine:
```bash
brew install ollama          # or https://ollama.com/download
ollama serve &               # local server on :11434
ollama pull llama3.1         # or qwen2.5, mistral, ...
export RESUME_TAILOR_PROVIDER=ollama
export OLLAMA_MODEL=llama3.1            # optional
resume-tailor "<job-url>"
```

### Debugging a failing LLM call
Add `-v` (info) or `-vv` (debug), or `export RESUME_TAILOR_LOG_LEVEL=DEBUG`, to see
provider requests, retries, rate-limit backoffs, and fallbacks. The pipeline never
writes a corrupted résumé: a malformed/failed LLM response leaves the résumé unchanged
and records a note.

### Add a new LLM backend
Copy the ready-to-fill stub `src/resume_tailor/llm/template_provider.py`, rename the
class, swap in your API call, then register it in
`src/resume_tailor/llm/base.py::get_provider` and select with `--provider <name>`.
Full step-by-step + contract rules in **[docs/ARCHITECTURE.md §4.4](docs/ARCHITECTURE.md)**.

---

## 6. Profile context corpus (optional)

Beyond the one-page résumé, you may have a folder of `.md`/`.txt` files with
additional achievements, projects, and details. The LLM can draw facts from this
corpus to surface more relevant experience for a given role — without ever
inventing.

Set `[profile] context_dir` in your config to point at a folder like:

```
profile/
├── README.md
├── 01_achievements.md
├── 02_opensource.md
└── 03_certifications.md
```

Files are read in sorted order; prefix them `01_`, `02_`, etc. to control priority.
Only facts present in your résumé **or** the profile corpus are allowed — the LLM
is instructed never to invent employers, titles, dates, degrees, or metrics.

---

## 7. How it stays safe

`src/resume_tailor/latex.py` validates every LLM edit before splicing:
- **Brace balance:** rejects any replacement with unbalanced `{`/`}`
- **No structural commands:** blocks `\section`, `\begin`, `\usepackage`, etc. that
  were not in the original — your LaTeX preamble and environments are inviolable
- **Shape preservation:** a paragraph region stays a paragraph; a bulleted list
  keeps its structure
- **Bullet count invariant:** the number of `\item` entries per region is locked
- **Line budget:** each region's *rendered line count* (estimated by stripping
  LaTeX markup) must not exceed the original — this is what keeps the résumé
  on one page
- **Readability guard:** the skills section must keep its bold-labeled category
  groups (≥3 groups if the original had 2+), preventing collapse into a run-on

Unsafe edits are skipped (region left unchanged) and reported in the run log.
The compiler (`compiler.py`) additionally fails the build if LaTeX logs any real
error — a broken render never passes silently.

### One-page compaction loop

Tailor → compile → measure page count. If the PDF overflows one page, re-tailor
with escalating "shorten" instructions (up to `max_compaction_passes`). If it
still doesn't fit, the run fails with a clear error (pass `--allow-overflow` to
accept a multi-page result).

---

## 8. Test

```bash
.venv/bin/python -m pytest          # 72 tests (~1s)
```
The suite is fully offline (no network, API key, or LaTeX engine required);
compiler/integration tests auto-skip when no LaTeX engine is present.

| Module | Tests | Guards |
|--------|-------|--------|
| `test_latex.py` | 21 | layout safety: region extraction, brace/structure/shape, splice |
| `test_tailor.py` | 9 | orchestration: safe/unsafe/partial edits, malformed LLM output |
| `test_cli.py` | 5 | end-to-end CLI: config precedence, per-job subdir, errors |
| `test_scripts.py` | 9 | shell scripts: syntax, executable bits, launcher error path, secret-ignore |
| `test_llm.py` | 9 | provider registry, model defaults/overrides, proxy |
| `test_scraper.py` | 8 | JSON-LD extraction, fallback, title/company cleanup |
| `test_config.py` | 5 | config precedence (CLI > env > file > default) |
| `test_compiler.py` | 4 | engine detect, error detection (skips w/o LaTeX) |
| `test_integration.py` | 2 | full pipeline wiring |

---

## Project layout

```
resume-tailor/
├── pyproject.toml              deps + pytest config + entry point (single source)
├── install.sh                  one-command setup / self-heal
├── uninstall.sh                clean removal
├── resume-tailor               launcher (env + venv + CLI)
├── resume-tailor.example.toml  documented config template
├── docs/
│   └── ARCHITECTURE.md         design decisions, provider model, extension guide
├── examples/
│   └── sample_resume.tex       generic resume with TAILOR markers (starter template)
├── profile/
│   └── README.md               profile corpus documentation
├── src/
│   └── resume_tailor/
│       ├── __init__.py         version
│       ├── cli.py              argument parsing + pipeline driver + compaction loop
│       ├── config.py           layered TOML config loader + profile corpus reader
│       ├── scraper.py          URL → JobPosting (JSON-LD + text fallback, JD prioritisation)
│       ├── latex.py            marker extraction + safety validation + byte-span splice
│       ├── tailor.py           prompt builder + LLM call + JSON parse + edit application
│       ├── compiler.py         pdflatex | latexmk | tectonic auto-detect
│       └── llm/
│           ├── __init__.py
│           ├── base.py         LLMProvider ABC + get_provider() registry
│           ├── gemini.py       default provider (free tier, 429 retry/backoff)
│           ├── openai_provider.py
│           ├── ollama.py       local/private provider
│           ├── echo.py         offline test provider
│           └── template_provider.py   ready-to-fill stub for new backends
└── tests/
    ├── conftest.py             fixtures (StubProvider, sample jobs)
    ├── fixtures/
    │   ├── sample_job.html     realistic Greenhouse job HTML
    │   └── sample_resume.tex   Jane Doe test resume with TAILOR markers
    └── test_*.py               72 tests
```

---

## Development

```bash
pip install -e ".[dev]"     # editable install + pytest (what install.sh does)
pytest                       # run the suite
```

Uses the modern `src/` layout so tests always run against the installed package,
never an accidental copy from the current directory.

---

## Known constraints / honest caveats

- **Gemini free tier is tight:** `gemini-2.5-pro` has 0 free quota; `gemini-2.5-flash`
  has 10 RPM / 250 per day; only `gemini-flash-lite-latest` is reliably free.
  For quality at no marginal cost, use `ollama` locally or enable Gemini billing.
- **Tectonic crashes on `fontawesome5`** on recent macOS — the tool prefers
  `pdflatex`/`latexmk`.
- **LinkedIn can't be scraped** (HTTP 999 bot-block, and it's against ToS) —
  copy the job text manually or use the profile corpus instead.
- **`rendered_lines()` is an estimate,** not a typesetter; the compaction loop
  is the true page-fit backstop.
