"""Orchestrates: regions + job context -> LLM -> validated edits."""
from __future__ import annotations

import json
import re

from .latex import Region, apply_edits, extract_regions, rendered_lines
from .llm.base import LLMProvider
from .scraper import JobPosting

_SYSTEM = """You are an expert resume editor optimizing a resume to pass automated \
applicant tracking systems (ATS) and impress human reviewers for a SPECIFIC job, \
WITHOUT changing the resume's structure or formatting.
 
Your goal: maximize relevant keyword overlap with the job description so the candidate \
scores highly, while staying truthful and within the layout.
 
Rules you MUST follow:
- Only rewrite the prose text of the regions you are given.
- Preserve all LaTeX commands and braces exactly; do not add \\section, \\begin, \
\\usepackage, or any structural command.
- Preserve each region's STRUCTURE: if a region is a paragraph (no \\item), keep it a \
paragraph; if it uses \\item bullets, keep the same bullet structure.
- CRITICAL — the resume MUST stay ONE page. Each rewritten region must fit within its \
max_lines budget and keep exactly its number of \\item bullets. Prefer concise, \
high-signal phrasing over padding.
- KEYWORDS — mirror the job description's EXACT wording for skills, tools, and \
responsibilities the candidate genuinely has. If the JD says "distributed systems", \
"observability", or "Kubernetes" and the candidate has that experience, use those exact \
terms (not synonyms). This is how the resume matches ATS keyword filters.
- SKILLS section specifically: reorder and surface the skills the JD asks for FIRST, so \
the most job-relevant skills the candidate has appear prominently. You MAY rename or \
regroup skill categories to align with the job (e.g. add a "Trust & Safety" group if the \
role emphasizes it), and you MAY add a skill ONLY if it clearly appears in the \
candidate's resume or profile context but was missing from the skills line. You MUST NOT \
add skills the candidate has no evidence for. Drop or de-emphasize the least relevant \
skills to stay within the line budget. \
READABILITY (REQUIRED): keep the skills grouped into 3-5 SHORT bold-labeled categories \
(e.g. \\textbf{Languages:} ... \\textbf{Trust \\& Safety:} ...), each a comma-separated \
list. NEVER output one undifferentiated run-on paragraph; preserve the bold category \
labels and grouped structure of the original skills block.
- Be truthful: rephrase, re-emphasize, and surface relevant existing experience. NEVER \
invent employers, titles, dates, degrees, metrics, or skills not present in the resume \
or profile context.
- Return STRICT JSON only: {"edits": {"<region_id>": "<new text>", ...}}. \
Omit regions you choose not to change.
- JSON ESCAPING: Since the values are LaTeX, you MUST escape all backslashes. \
Every single LaTeX backslash must be represented as '\\\\' in the JSON string. \
(e.g., '\\textbf' becomes '\\\\textbf'). This is mandatory to prevent JSON parse errors.
"""


def _build_user_prompt(job: JobPosting, regions: list[Region], shorten: int = 0,
                       profile_context: str = "") -> str:
    region_payload = {
        r.rid: {
            "text": r.text,
            "max_lines": rendered_lines(r.text),
            "bullets": r.text.count("\\item"),
        }
        for r in regions
    }
    extra = ""
    if shorten == 1:
        extra = (
            "\n\nIMPORTANT: the previous attempt did NOT fit on one page. Make the "
            "prose meaningfully SHORTER this time — trim weak words, merge clauses, "
            "drop the least-relevant details. Keep every bullet but make each tighter."
        )
    elif shorten >= 2:
        extra = (
            "\n\nIMPORTANT: the resume STILL overflows one page. Be aggressive: cut "
            "each bullet to its essential, highest-impact form. Remove adjectives and "
            "filler, keep concrete metrics. Target roughly "
            f"{max(40, 90 - shorten * 15)}% of the original length per region."
        )
    profile_block = ""
    if profile_context.strip():
        profile_block = (
            "\n\nCANDIDATE PROFILE (reference corpus — additional real achievements "
            "and details that did NOT fit on the one-page resume). You MAY draw "
            "facts, metrics, and experience from here to better match the role when "
            "the resume's existing text is weaker for this job. You MUST NOT invent "
            "anything not present in either this corpus or the resume. Only use items "
            "genuinely relevant to the job above.\n"
            f"{profile_context}"
        )
    return (
        f"{job.as_prompt_context()}"
        f"{profile_block}\n\n"
        "Below are the editable resume regions as JSON (id -> current LaTeX text). "
        "Rewrite the prose to target the role above, following all rules. Prioritise "
        "matching the job's required SKILLS first, then its REQUIREMENTS, then the "
        "ROLE responsibilities."
        f"{extra}\n\n"
        f"REGIONS_JSON: {json.dumps(region_payload, ensure_ascii=False)}"
    )


def _parse_edits(raw: str) -> dict[str, str]:
    raw = raw.strip()
    # strip code fences if the model added them
    raw = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw, flags=re.DOTALL)
    raw = raw.strip()
    # Models sometimes emit the JSON object followed by trailing prose or a
    # second object ("Extra data" errors). Decode just the first JSON value and
    # ignore anything after it; if that fails, grab the first {...} span.
    try:
        data, _ = json.JSONDecoder().raw_decode(raw)
    except json.JSONDecodeError as e:
        # LOG RAW OUTPUT FOR DEBUGGING
        with open("debug_llm_output.txt", "w") as f:
            f.write(raw)
        if "Invalid \\escape" in str(e):
            raw = re.sub(r'\\(?![\\\"\/nu])', r'\\\\', raw)
            try:
                data, _ = json.JSONDecoder().raw_decode(raw)
            except json.JSONDecodeError:
                start = raw.find("{")
                end = raw.rfind("}")
                if start == -1 or end <= start:
                    raise
                data = json.loads(raw[start : end + 1])
        else:
            start = raw.find("{")
            end = raw.rfind("}")
            if start == -1 or end <= start:
                raise
            data = json.loads(raw[start : end + 1])
    edits = data.get("edits", data) if isinstance(data, dict) else {}
    return {str(k): str(v) for k, v in edits.items()}


def _parse_naming(job: JobPosting, provider: LLMProvider) -> tuple[str, str]:
    """Use LLM to extract a clean company name and a short job identifier.
    Prioritize extracting a numeric Job ID from the description or URL if available."""
    system = "You are a helpful assistant that extracts structural information from job postings. Return STRICT JSON only."
    user = f"From this job posting (title: {job.title}, company: {job.company}, URL: {job.url}), identify the actual legal company name and a concise job identifier. If a specific numeric Job ID (e.g., '19585') exists in the text or URL, include it in the job_id (e.g., 'SRE-19585').\n\nReturn JSON: {{\"company\": \"...\", \"job_id\": \"...\"}}"
    
    try:
        raw = provider.complete(system, user)
        data = _parse_edits(raw)
        return (
            data.get("company", job.company),
            data.get("job_id", job.title.split("-")[0].strip())
        )
    except Exception:
        return job.company, job.title.split("-")[0].strip()

def tailor_tex(
    tex: str,
    job: JobPosting,
    provider: LLMProvider,
    shorten: int = 0,
    profile_context: str = "",
) -> tuple[str, dict]:
    """Return (tailored_tex, report).
 
    shorten: 0 = normal; 1 = ask for tighter prose; >=2 = aggressively shorten.
    Used by the CLI's one-page compaction loop to retry when the PDF overflows.
 
    profile_context: optional reference corpus of extra achievements/content
    (not on the resume). The LLM may draw FACTS from it to better match the
    role, but must never invent — enforced in the system prompt.
    """
    regions = extract_regions(tex)
    report: dict = {
        "regions_found": len(regions),
        "region_kind": regions[0].kind if regions else None,
        "edits_applied": 0,
        "notes": [],
    }
    if not regions:
        report["notes"].append(
            "No tailorable regions found. Add % <TAILOR:name> ... % </TAILOR> "
            "markers or \\item bullets."
        )
        return tex, report
 
    user = _build_user_prompt(job, regions, shorten=shorten,
                               profile_context=profile_context)
    
    # Try up to 3 times if JSON parsing fails
    for attempt in range(3):
        raw = provider.complete(_SYSTEM, user)
        try:
            edits = _parse_edits(raw)
            break
        except json.JSONDecodeError as exc:
            if attempt == 2:
                report["notes"].append(f"LLM returned non-JSON after 3 attempts: {exc}")
                return tex, report
            report["notes"].append(f"JSON parse failed (attempt {attempt+1}), retrying...")

    new_tex, notes = apply_edits(tex, regions, edits)
    report["edits_applied"] = sum(
        1 for r in regions if r.rid in edits and f"skipped {r.rid}" not in " ".join(notes)
    )
    report["notes"].extend(notes)
    return new_tex, report
