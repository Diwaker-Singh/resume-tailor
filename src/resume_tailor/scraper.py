"""Job posting scraper. Extracts clean job text from any URL.

Strategy:
  1. Fetch HTML (via system proxy env if present — works behind fwdproxy).
  2. Prefer JSON-LD schema.org/JobPosting (Greenhouse/Lever/Ashby/Workday emit it).
  3. Fall back to main-content text extraction.
"""
from __future__ import annotations

import html
import json
import os
import re
from dataclasses import dataclass, field

import httpx
from bs4 import BeautifulSoup

_UA = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)


@dataclass
class JobPosting:
    url: str
    title: str = ""
    company: str = ""
    location: str = ""
    description: str = ""          # cleaned plain text
    raw_json_ld: dict = field(default_factory=dict)

    @property
    def source(self) -> str:
        """How the data was obtained: 'json-ld' (structured, high confidence)
        or 'text-fallback' (heuristic main-content extraction)."""
        return "json-ld" if self.raw_json_ld else "text-fallback"

    def as_dict(self) -> dict:
        """Serializable snapshot of everything the scraper pulled, including a
        confidence signal so you can audit whether the scrape was complete."""
        return {
            "url": self.url,
            "title": self.title,
            "company": self.company,
            "location": self.location,
            "source": self.source,
            "fields_found": {
                "title": bool(self.title),
                "company": bool(self.company),
                "location": bool(self.location),
                "description": bool(self.description),
                "json_ld": bool(self.raw_json_ld),
            },
            "description_chars": len(self.description),
            "description": self.description,
            "raw_json_ld": self.raw_json_ld,
        }

    def as_prompt_context(self, max_chars: int = 8000) -> str:
        parts = []
        if self.title:
            parts.append(f"Job Title: {self.title}")
        if self.company:
            parts.append(f"Company: {self.company}")
        if self.location:
            parts.append(f"Location: {self.location}")
        # Prioritise the JD *before* truncation so the char budget keeps the
        # high-value sections (skills > requirements > role) and drops the
        # least-important content (perks, EEO boilerplate) first.
        desc = prioritize_jd(self.description, max_chars)
        parts.append(f"\nJob Description (ordered by relevance):\n{desc}")
        return "\n".join(parts)


def _strip_html(raw: str) -> str:
    soup = BeautifulSoup(raw, "lxml")
    text = soup.get_text("\n")
    text = html.unescape(text)
    lines = [ln.strip() for ln in text.splitlines()]
    lines = [ln for ln in lines if ln]
    # collapse runs of duplicate blank-ish lines
    return "\n".join(lines)


def _walk_json_ld(obj):
    """Yield every dict in a possibly-nested JSON-LD structure."""
    if isinstance(obj, dict):
        yield obj
        for v in obj.values():
            yield from _walk_json_ld(v)
    elif isinstance(obj, list):
        for v in obj:
            yield from _walk_json_ld(v)


def _extract_json_ld_job(soup: BeautifulSoup) -> dict | None:
    for tag in soup.find_all("script", {"type": "application/ld+json"}):
        if not tag.string:
            continue
        try:
            data = json.loads(tag.string)
        except (json.JSONDecodeError, TypeError):
            # some sites embed multiple JSON objects or trailing commas
            try:
                data = json.loads(re.sub(r",\s*}", "}", tag.string))
            except Exception:
                continue
        for node in _walk_json_ld(data):
            t = node.get("@type", "")
            if t == "JobPosting" or (isinstance(t, list) and "JobPosting" in t):
                return node
    return None


def _proxy() -> str | None:
    """Return HTTPS proxy from env, if any (fwdproxy-friendly).

    We read the proxy explicitly rather than via httpx trust_env, because some
    environments (e.g. Meta devvm) set a NO_PROXY containing IPv6 literals like
    [::1] that break httpx's env parsing.
    """
    return (
        os.environ.get("https_proxy")
        or os.environ.get("HTTPS_PROXY")
        or os.environ.get("http_proxy")
        or os.environ.get("HTTP_PROXY")
    )


def fetch(url: str, timeout: float = 30.0) -> str:
    """Fetch raw HTML. Honors HTTP(S)_PROXY env (fwdproxy-friendly)."""
    headers = {"User-Agent": _UA, "Accept-Language": "en-US,en;q=0.9"}
    with httpx.Client(
        follow_redirects=True,
        timeout=timeout,
        headers=headers,
        trust_env=False,
        proxy=_proxy(),
    ) as client:
        resp = client.get(url)
        resp.raise_for_status()
        return resp.text




# ---- JD prioritisation ----------------------------------------------------

# Priority tiers (lower number = kept first under a char budget). The ranking is
# Skills > Requirements/Qualifications > Role/Responsibilities > everything else.
_TIER_PATTERNS = [
    (1, re.compile(r"\b(skills?|technolog|tech stack|proficien|expertise|"
                   r"languages?|tools?|frameworks?)\b", re.I)),
    (2, re.compile(r"\b(requirements?|qualifications?|must[- ]have|"
                   r"you (?:have|bring|should have)|we(?:'| a)re looking for|"
                   r"minimum|basic qualifications|preferred)\b", re.I)),
    (3, re.compile(r"\b(responsibilit|role|what you(?:'| wi)ll do|about the (?:role|job)|"
                   r"the (?:role|opportunity)|day[- ]to[- ]day|impact)\b", re.I)),
]
_TIER_OTHER = 4

# Boilerplate we actively de-prioritise (dropped first when over budget).
_TIER_BOILERPLATE = 5
_BOILERPLATE_RE = re.compile(
    r"\b(equal opportunity|eeo|accommodation|benefits|perks|diversity|"
    r"we offer|salary range|compensation|privacy policy|apply now|"
    r"about (?:us|the company)|our mission)\b", re.I,
)


_HEADING_RE = re.compile(
    r"^\s*(?:[A-Z][\w&/ -]{0,40}:\s*$"          # "Responsibilities:" style
    r"|(?:About|Responsibilities|Requirements|Qualifications|Skills|"
    r"What you|You may|Strong candidates|Benefits|Perks|The Role|Who you|"
    r"Minimum|Preferred|Nice to have|About the role|About us)\b.*)",
    re.I,
)


def _is_heading(line: str) -> bool:
    """Heuristic: a short line that introduces a section (ends in ':' or is a
    known section title). Used to segment JDs that have no blank lines."""
    s = line.strip()
    if not s or len(s) > 60:
        return False
    if s.endswith(":"):
        return True
    return bool(_HEADING_RE.match(s))


def _split_blocks(text: str) -> list[str]:
    """Split JD into section blocks. Primary boundary is a blank line; for JDs
    that have no blank lines (common with text-fallback scrapes), a heading-like
    line also starts a new block, so each section becomes its own block."""
    blocks, cur = [], []

    def flush():
        if cur:
            blocks.append("\n".join(cur))
            cur.clear()

    for line in text.splitlines():
        if not line.strip():
            flush()
            continue
        # a heading starts a new block (but only if we've already collected body)
        if _is_heading(line) and cur:
            flush()
        cur.append(line)
    flush()

    # merge a lone heading line with the block that follows it
    merged: list[str] = []
    i = 0
    while i < len(blocks):
        b = blocks[i]
        only_heading = "\n" not in b and _is_heading(b)
        if only_heading and i + 1 < len(blocks):
            merged.append(b + "\n" + blocks[i + 1]); i += 2
        else:
            merged.append(b); i += 1
    return merged


def _tier_of(block: str) -> int:
    if _BOILERPLATE_RE.search(block):
        return _TIER_BOILERPLATE
    for tier, pat in _TIER_PATTERNS:
        if pat.search(block):
            return tier
    return _TIER_OTHER


def prioritize_jd(description: str, max_chars: int) -> str:
    """Reorder JD blocks by relevance (skills > requirements > role > other >
    boilerplate) and pack into max_chars, so truncation drops the least
    important content first. Original order is preserved within each tier."""
    desc = (description or "").strip()
    if len(desc) <= max_chars:
        return desc
    blocks = _split_blocks(desc)
    # stable sort by tier (Python's sort is stable -> keeps in-tier order)
    ordered = sorted(enumerate(blocks), key=lambda iv: (_tier_of(iv[1]), iv[0]))
    out, used = [], 0
    for _, block in ordered:
        if used + len(block) + 2 > max_chars:
            continue  # skip this block, try the next (smaller) one
        out.append(block); used += len(block) + 2
    if not out:  # pathological: one giant block — hard cut
        return desc[:max_chars] + "\n...[truncated]"
    return "\n\n".join(out) + "\n...[lower-priority content truncated]"


def scrape(url: str, *, html_text: str | None = None) -> JobPosting:
    """Scrape a job posting. Pass html_text to bypass the network (testing)."""
    raw = html_text if html_text is not None else fetch(url)
    soup = BeautifulSoup(raw, "lxml")

    job = JobPosting(url=url)
    node = _extract_json_ld_job(soup)
    if node:
        job.raw_json_ld = node
        job.title = (node.get("title") or "").strip()
        hiring = node.get("hiringOrganization") or {}
        if isinstance(hiring, dict):
            job.company = (hiring.get("name") or "").strip()
        loc = node.get("jobLocation") or {}
        if isinstance(loc, list) and loc:
            loc = loc[0]
        if isinstance(loc, dict):
            addr = loc.get("address") or {}
            if isinstance(addr, dict):
                job.location = (
                    addr.get("addressLocality")
                    or addr.get("addressRegion")
                    or addr.get("addressCountry")
                    or ""
                )
        desc_html = node.get("description") or ""
        job.description = _strip_html(desc_html)

    # Fallback / augment if JSON-LD gave us little.
    if len(job.description) < 200:
        # try common content containers, else whole body
        main = (
            soup.find("main")
            or soup.find(attrs={"role": "main"})
            or soup.find("article")
            or soup.body
            or soup
        )
        text = _strip_html(str(main))
        if len(text) > len(job.description):
            job.description = text
        if not job.title and soup.title and soup.title.string:
            job.title = soup.title.string.strip()

    _clean_title_company(job, soup)
    return job


# Boilerplate that wraps the real title on common ATS pages, e.g.
# "Job Application for Software Engineer, Safeguards at Anthropic".
_TITLE_AT_RE = re.compile(
    r"^(?:job application for\s+|bewerbung als\s+|candidature pour\s+|"
    r"solicitud de empleo para\s+)?(?P<title>.+?)\s+(?:at|bei|chez|en)\s+"
    r"(?P<company>.+?)\s*$",
    re.IGNORECASE,
)
_TITLE_PREFIX_RE = re.compile(
    r"^\s*(?:job application for|bewerbung als|candidature pour|"
    r"solicitud de empleo para)\s+",
    re.IGNORECASE,
)


def _clean_title_company(job: JobPosting, soup: BeautifulSoup) -> None:
    """Strip ATS boilerplate from the title and backfill company when missing."""
    title = (job.title or "").strip()

    # "<title> at <company>" — split out a company if we don't have one.
    m = _TITLE_AT_RE.match(title)
    if m:
        job.title = m.group("title").strip()
        if not job.company:
            job.company = m.group("company").strip()
    else:
        # at least drop a leading "Job Application for "
        job.title = _TITLE_PREFIX_RE.sub("", title).strip()

    # Backfill company from OpenGraph site name if still missing.
    if not job.company:
        og = soup.find("meta", property="og:site_name")
        if og and og.get("content"):
            job.company = og["content"].strip()
