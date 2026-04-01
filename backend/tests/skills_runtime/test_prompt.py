from app.services.skills_runtime.models import SkillEntry
from app.services.skills_runtime.prompt import build_skill_prompt_state


def _entry(slug: str) -> SkillEntry:
    return SkillEntry(
        slug=slug,
        name=slug,
        description=f"{slug} description",
        skill_file=f"/tmp/skill_library/{slug}/SKILL.md",
        folder_path=f"/tmp/skill_library/{slug}",
    )


def test_build_skill_prompt_state_renders_deterministic_skill_catalog():
    state = build_skill_prompt_state(entries=[_entry("beta"), _entry("alpha")])

    assert "<available_skills>" in state.prompt
    assert state.prompt.index("<name>alpha</name>") < state.prompt.index("<name>beta</name>")
    rendered = state.prompt.replace("\\", "/")
    assert "<skill_file_path>/tmp/skill_library/alpha/SKILL.md</skill_file_path>" in rendered
    assert "<references_root>/tmp/skill_library/alpha/references</references_root>" in rendered
