# Profile context corpus

Drop `.md` / `.txt` files here with achievements, projects, and details that
DON'T fit on your one-page resume. The tailor will pull relevant facts from
these when a job calls for experience your resume doesn't already surface.

Rules:
- Facts only — the LLM is instructed never to invent; everything it uses must
  exist here or in your resume.
- Prefix files `01_`, `02_`, ... to control read order (earlier = higher priority).
- Enable by setting `[profile] context_dir` in resume-tailor.toml to this folder.
