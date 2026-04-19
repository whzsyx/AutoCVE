from __future__ import annotations


def build_finding_skill_protocol() -> str:
    return """
## Finding Skill Protocol

- Bootstrap the current primary audit skill before relying on any of its rules.
- Read skill materials through the runtime tool schema and tool-call interface, not the legacy scanner tool names.
- Prefer the canonical runtime tools for skill reading: use `Read` on the catalog's `skill_file_path`, then `Glob` or `Grep` under `references_root` when you need supporting references.
- When comparing related source/sink/controller/service/mapper/xml files, prefer a small number of targeted `Read` calls after locating files with `Glob`/`Grep`, instead of looping file-by-file with historical batch-reading prompts.
- Use `Write` only when you are explicitly asked to create or update an artifact. Use `Bash` / `PowerShell` only when shell access is truly necessary for evidence collection.
- Use `Skill` to bootstrap the relevant audit skill and keep the loaded guidance aligned with the active runtime catalog.
- Do not loop on skill materials. After reading `SKILL.md` and one or two core references, switch back to project code.
- Treat skill references as just-in-time guidance, not a preload checklist you must exhaust before auditing code.
- Do not rely on scanner-style tools as primary evidence. Findings must come from direct code reading plus the loaded skill materials.
""".strip()
