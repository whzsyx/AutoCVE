import json

from app.services.skill_file_service import SkillFileService


def test_skill_file_service_write_and_read_stay_on_canonical_skill_root(tmp_path, monkeypatch):
    monkeypatch.setattr(SkillFileService, "project_root", classmethod(lambda cls: tmp_path))

    skill = SkillFileService.write_skill(
        slug="alpha",
        name="alpha",
        description="Alpha skill",
        content="# Alpha",
        tags=["finding"],
    )

    assert skill["slug"] == "alpha"
    assert (tmp_path / "skill_library" / "alpha" / "SKILL.md").exists()
    assert not (tmp_path / "skill_library" / "agents" / "finding" / "alpha").exists()


def test_skill_file_service_binding_refresh_no_longer_creates_agent_skill_mirror(tmp_path, monkeypatch):
    monkeypatch.setattr(SkillFileService, "project_root", classmethod(lambda cls: tmp_path))

    SkillFileService.write_skill(
        slug="alpha",
        name="alpha",
        description="Alpha skill",
        content="# Alpha",
        tags=["finding"],
    )

    binding = SkillFileService.upsert_binding(
        "finding",
        "alpha",
        enabled=True,
        always_include=True,
        sort_order=1,
        match_keywords=["auth"],
    )

    aggregated = json.loads((tmp_path / "skill_library" / "alpha" / "bindings.json").read_text(encoding="utf-8"))

    assert binding["skill_id"] == "alpha"
    assert aggregated[0]["skill_id"] == "alpha"
    assert aggregated[0]["skill_file"].replace("\\", "/").endswith("skill_library/alpha/SKILL.md")
    assert aggregated[0]["workspace_relative_path"] == "skill_library/alpha"
    assert not (tmp_path / "skill_library" / "agents" / "finding" / "alpha").exists()
