from __future__ import annotations


def build_finding_skill_protocol() -> str:
    return """
## Finding Skill Protocol

- Bootstrap the current primary audit skill before relying on any of its rules.
- Prefer generic file tools for skill reading: use `read_file` on the catalog's `skill_file_path`, then `list_files` or `read_many_files` under `references_root`.
- When comparing related source/sink/controller/service/mapper/xml files, prefer `read_many_files` or `Action Batch` over one-file-per-loop reading.
- Do not loop on skill materials. After reading `SKILL.md` and one or two core references, switch back to project code.
- Treat skill references as just-in-time guidance, not a preload checklist you must exhaust before auditing code.
- Do not rely on scanner-style tools as primary evidence. Findings must come from direct code reading plus the loaded skill materials.
""".strip()
