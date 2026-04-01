import json

import pytest

from app.services.init_agent_assets import init_agent_assets
from app.services.skill_file_service import SkillFileService


@pytest.mark.asyncio
async def test_init_agent_assets_stays_local_and_binds_canonical_skill(tmp_path, monkeypatch):
    monkeypatch.setattr(SkillFileService, "project_root", classmethod(lambda cls: tmp_path))

    skill_dir = tmp_path / "skill_library" / "code-audit-finding"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        "---\n"
        "name: code-audit-finding\n"
        "description: Local bundled finding skill\n"
        "tags: [finding, code-audit]\n"
        "---\n\n"
        "# code-audit-finding\n",
        encoding="utf-8",
    )

    async def fail_import(*args, **kwargs):
        raise AssertionError("init_agent_assets should not import GitHub skills")

    monkeypatch.setattr(SkillFileService, "import_github_skill", classmethod(fail_import))

    await init_agent_assets()

    bindings = json.loads(
        (tmp_path / "skill_library" / "agents" / "finding" / "bindings.json").read_text(encoding="utf-8")
    )
    assert bindings["skills"][0]["slug"] == "code-audit-finding"
    assert bindings["skills"][0]["always_include"] is True
    assert not (tmp_path / "skill_library" / "agents" / "finding" / "code-audit-finding").exists()
