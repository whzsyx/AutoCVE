from __future__ import annotations

from .models import SkillEntry, SkillPromptState, SkillRoutePlan


def build_skill_prompt_state(
    entries: list[SkillEntry],
    matched: list[SkillEntry] | None = None,
    route_plan: SkillRoutePlan | None = None,
) -> SkillPromptState:
    available_entries = list(entries)
    ordered_entries = sorted(available_entries, key=lambda entry: entry.name.lower())
    matched = matched or []
    if not available_entries:
        return SkillPromptState(entries=[], matched=matched, prompt="", route_plan=route_plan or SkillRoutePlan())

    lines = ["<available_skills>"]
    for entry in ordered_entries:
        lines.append("<skill>")
        lines.append(f"<name>{entry.name}</name>")
        lines.append(f"<description>{entry.description}</description>")
        lines.append(f"<skill_root>{entry.folder_path}</skill_root>")
        lines.append(f"<skill_file_path>{entry.skill_file}</skill_file_path>")
        lines.append(f"<references_root>{entry.folder_path}/references</references_root>")
        lines.append(f"<examples_root>{entry.folder_path}/examples</examples_root>")
        lines.append(f"<scripts_root>{entry.folder_path}/scripts</scripts_root>")
        lines.append("</skill>")
    lines.append("</available_skills>")
    return SkillPromptState(
        entries=available_entries,
        matched=matched,
        prompt="\n".join(lines),
        route_plan=route_plan or SkillRoutePlan(),
    )
