# Architecture & Design Decisions

How resume-tailor works, why it's built this way, and how to extend it.

---

## 1. What it does

Given a **job posting URL** and a **LaTeX résumé**, it scrapes the posting, uses a
pluggable LLM to rewrite only the job-specific prose, and compiles a tailored PDF
that stays **one page** — without ever altering the résumé's structure or layout.

```
job URL ─► scrape ─► prioritise JD ─► LLM tailors marked prose ─► splice ─► compile ─► 1-page PDF
                                            ▲                         │
                                   profile corpus              page-fit loop
                                   (extra achievements)        (re-tailor shorter)
```

---

## 2. Core design decisions (and the why)

### 2.1 Marker-scoped editing — the layout-safety guarantee
**Decision:** the LLM only ever sees and edits text inside `% <TAILOR:name> … % </TAILOR>`
comment markers; it receives a `{region_id: text}` map and returns `{region_id: new_text}`.

**Why:** the alternative (let the LLM rewrite the whole `.tex`) breaks layout ~1 in 5
times — unbalanced braces, hallucinated `\usepackage`, mangled environments. By never
sending structure, **structure cannot break**. Markers are LaTeX comments, so the résumé
renders identically with or without them.

**Rejected alternatives:** whole-file rewrite + repair loop (flexible but fragile/costly);
Jinja-style templating (clean but requires re-authoring the résumé).

### 2.2 Splice by exact byte-span substitution
`apply_edits()` replaces each region's byte span right-to-left (so offsets stay valid).
Everything outside a region is preserved byte-for-byte.

### 2.3 Edits are validated before they're applied
`is_safe_replacement()` rejects any rewrite that would:
- unbalance braces,
- introduce a structural command (`\section`, `\begin`, `\usepackage`, …) not in the original,
- change a paragraph region into bullets or vice-versa,
- change the bullet count,
- **exceed the region's rendered line budget** (see 2.4).
Rejected edits are skipped (region keeps its original text) and noted in the report.

### 2.4 Size by **rendered lines**, not characters
**Decision:** a rewrite may not render to more lines than the original region.

**Why:** page-fit is a *vertical-space* property. A char cap is blind to it (and once
rejected a 1-char overage). `rendered_lines()` strips LaTeX markup (commands, math, braces)
to estimate *visible* text, then counts wrapped lines per bullet/paragraph.

**The elegant part:** original and new are measured with the **same** estimator, so the
chars-per-line constant (95) needn't be exact — any error cancels in the comparison.
Self-calibrating. Per-region budgets are also passed to the LLM (`max_lines`, `bullets`)
so it aims correctly on the first pass.

### 2.5 One-page compaction loop — the real backstop
Prompt constraints can't *guarantee* one page; only rendering can. So: tailor → compile →
`page_count()` + `vertical_overflow()` → if it overflows, re-tailor asking for shorter
prose (escalating aggressiveness) up to `max_compaction_passes`, then hard-fail unless
`--allow-overflow`.

### 2.6 JD prioritisation **before** truncation
**Decision:** segment the JD into blocks, reorder by relevance tier
(**skills > requirements > role > other > boilerplate**), then pack into the char budget.

**Why:** the JD is truncated to fit the prompt budget. A naive `[:8000]` chops the tail —
where "Requirements" often live. Prioritising first means truncation drops boilerplate
(perks, EEO, "About us") and **never** skills/requirements. The block splitter is
heading-aware (handles real scrapes that have no blank lines, only heading lines).

### 2.7 Profile context corpus — boost beyond the résumé
**Decision:** an optional `profile/` folder of `.md`/`.txt` files (achievements that don't
fit on one page). Fed to the LLM as a **reference corpus** it may draw facts from.

**Why:** without it, the LLM can only *rephrase* what's on the page. With it, it can
**surface** a more relevant achievement you have but didn't list. Hard rule in the system
prompt: it may use facts from the corpus or résumé only — **never invent**.

### 2.8 Truthfulness guardrail
The system prompt forbids inventing employers, titles, dates, degrees, or metrics. The
profile corpus and résumé are the only allowed fact sources. Temperature is low (0.4).

### 2.9 Scrape auditability
Every run writes `job.json` to the output folder: title, company, the cleaned JD, and a
`source` field (`json-ld` = high confidence vs `text-fallback` = heuristic) so you can
verify the scrape captured the real posting.

---

## 3. Pipeline (module by module)

| Module | Responsibility |
|--------|----------------|
| `scraper.py` | URL → `JobPosting` (JSON-LD schema.org/JobPosting first, text fallback); title/company cleanup; `prioritize_jd()` |
| `latex.py` | `extract_regions()`, `is_safe_replacement()`, `rendered_lines()`, `apply_edits()` |
| `tailor.py` | builds the system+user prompt, calls the provider, parses JSON edits (tolerates trailing data), applies them |
| `compiler.py` | engine auto-detect (`pdflatex`→`latexmk`→`tectonic`), compile, fail on LaTeX errors, `page_count()`, `vertical_overflow()` |
| `config.py` | layered TOML config (CLI > env > file > default), profile-corpus loader |
| `logging_setup.py` | central logger; `-v/-vv` or `$RESUME_TAILOR_LOG_LEVEL` |
| `cli.py` | argument parsing + the compaction loop driver |
| `llm/` | provider ABC + registry + gemini/openai/ollama/echo |

---

## 4. LLM provider model

### 4.1 The contract
Every provider implements one method:
```python
class LLMProvider(abc.ABC):
    name: str
    def complete(self, system: str, user: str) -> str: ...   # returns JSON text
```
The factory `get_provider(name, **kwargs)` maps a name to a provider. Selection precedence:
`--provider` flag > `$RESUME_TAILOR_PROVIDER` > config `[llm] provider` > default (`gemini`).

### 4.2 Built-in providers
| Name | Use | Key/Setup |
|------|-----|-----------|
| `gemini` | default; free tier | `GEMINI_API_KEY`; model `gemini-flash-lite-latest` (only reliably-free model) |
| `openai` | paid, high quality | `OPENAI_API_KEY` |
| `ollama` | **local, private, no key/quota** | `ollama serve` + `ollama pull <model>` |
| `echo` | offline tests (no network) | none — echoes regions unchanged |

### 4.3 What happens when a provider fails
- **Gemini 429 (quota):** retries with backoff (`_BACKOFF`), logs each retry at WARNING,
  then raises a clear "free-tier quota exhausted; try later or set GEMINI_MODEL" error.
- **Malformed JSON:** `_parse_edits()` strips code fences and decodes the first JSON value
  (tolerates trailing prose / a second object). If still unparseable, the run keeps the
  résumé unchanged and records a note — it never writes a corrupted `.tex`.
- **Ollama unreachable:** raises "Ollama not reachable at <host>; run `ollama serve`".
- **Any unsafe edit:** skipped per-region (original kept) and logged — never fatal.

Turn on diagnostics with `-v` (INFO) or `-vv` (DEBUG), or `RESUME_TAILOR_LOG_LEVEL=DEBUG`.

### 4.4 How to add a new LLM provider (≈20 lines)

**Fastest path:** copy the ready-to-fill stub `src/resume_tailor/llm/template_provider.py`
— it has the full contract, env-var handling, proxy guidance, and error handling already
wired; you just rename the class and swap in your API call.

**The 3 steps:**

1. **Create the provider.** Copy `template_provider.py` to
   `src/resume_tailor/llm/<yourname>.py`, rename the class, and replace the API
   call in `complete()`:
   ```python
   import os, httpx
   from ..logging_setup import get_logger
   from .base import LLMProvider, proxy_from_env
   _log = get_logger("llm.myllm")

   class MyLLMProvider(LLMProvider):
       name = "myllm"
       def __init__(self, model=None, api_key=None):
           self.model = model or os.environ.get("MYLLM_MODEL", "default-model")
           self.api_key = api_key or os.environ.get("MYLLM_API_KEY", "")
       def complete(self, system: str, user: str) -> str:
           with httpx.Client(timeout=120, trust_env=False, proxy=proxy_from_env()) as c:
               r = c.post("https://api.myllm.com/v1/chat",
                          headers={"Authorization": f"Bearer {self.api_key}"},
                          json={"model": self.model, "messages": [
                              {"role": "system", "content": system},
                              {"role": "user", "content": user}]})
               r.raise_for_status()
           return r.json()["choices"][0]["message"]["content"]  # adapt to your API
   ```

2. **Register it** in `src/resume_tailor/llm/base.py::get_provider`:
   ```python
   if name == "myllm":
       from .myllm import MyLLMProvider
       return MyLLMProvider(**kwargs)
   ```

3. **Select it:**
   ```bash
   resume-tailor "<url>" --provider myllm          # one-off
   export RESUME_TAILOR_PROVIDER=myllm             # persistent
   export MYLLM_API_KEY=...   MYLLM_MODEL=...
   ```

4. **Add a test** mirroring `tests/test_llm.py::test_get_ollama_provider`.

Everything else (caching, the one-page compaction loop, JSON parsing, `.bak`
re-run safety) keeps working because it only depends on the `complete()` contract.

**Contract notes / rules:**
| Rule | Why |
|------|-----|
| `complete()` returns the model's **text** | The orchestrator parses JSON out of it — JSON-mode preferred, but it tolerates code fences + trailing prose |
| Honor a `model` kwarg | So `--model` works |
| Read secrets from **env**, not the config file | Keeps keys out of git |
| `proxy=proxy_from_env()` for external APIs; **none** for local servers | Works behind corporate/fwdproxy; local (Ollama) needs no proxy |
| Log via `get_logger("llm.<name>")` | `-v`/`-vv` diagnostics |
| Raise a clear `RuntimeError` on failure | Pipeline degrades gracefully (résumé kept unchanged) |

---

## 5. Configuration & precedence
`CLI flag > environment variable > resume-tailor.toml > built-in default`.
See `resume-tailor.example.toml` for every key. Secrets live in `.env.local` (gitignored),
never in the config file.

---

## 6. Testing philosophy
72 tests, fully offline (the `echo` provider + local HTML fixtures, `--no-pdf` where no
LaTeX engine). Compiler/integration tests auto-skip without a LaTeX engine. The suite is the
regression guard: layout-safety invariants, JD prioritisation, line budgets, config
precedence, provider registry, the shell scripts, and end-to-end pipeline wiring.

---

## 7. Known constraints / honest caveats
- **Gemini free tier is tight:** 2.5-pro = 0 free quota; 2.5-flash = 10 RPM/250 per day;
  only `gemini-flash-lite-latest` is reliably free. For quality at no marginal cost, use
  `ollama` locally or enable Gemini billing and switch to `gemini-2.5-pro`.
- **Tectonic crashes on `fontawesome5`** on recent macOS — the tool prefers `pdflatex`.
- **LinkedIn can't be scraped** (HTTP 999 bot-block, and it's against ToS) — populate the
  profile corpus from your own records instead.
- **`rendered_lines()` is an estimate**, not a typesetter; the compaction loop is the true
  page-fit backstop.
