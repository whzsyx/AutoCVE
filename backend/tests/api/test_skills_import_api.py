import pytest

from app.api.v1.endpoints import skills as skills_endpoint
from app.schemas.skill import SkillImportRequest
from app.services.agent.skill_service import SkillService
from app.services.skill_file_service import SkillFileService


@pytest.mark.asyncio
async def test_import_github_skill_endpoint_keeps_installed_directory_content(tmp_path, monkeypatch):
    monkeypatch.setattr(SkillFileService, "project_root", classmethod(lambda cls: tmp_path))
    skill_dir = tmp_path / "skill_library" / "demo-skill"
    (skill_dir / "references").mkdir(parents=True)
    original_content = (
        "---\n"
        "name: Demo Skill\n"
        "description: Demo description\n"
        "---\n\n"
        "# Demo\n"
    )
    (skill_dir / "SKILL.md").write_text(original_content, encoding="utf-8")
    (skill_dir / "references" / "guide.md").write_text("guide", encoding="utf-8")
    SkillFileService.sync_all()

    async def fake_import(cls, repo_url: str):
        assert repo_url == "https://github.com/example/repo/tree/main/skills/demo-skill"
        return SkillFileService.read_skill("demo-skill")

    monkeypatch.setattr(SkillService, "import_github_skill", classmethod(fake_import))

    response = await skills_endpoint.import_github_skill(
        SkillImportRequest(
            repo_url="https://github.com/example/repo/tree/main/skills/demo-skill",
            agent_type="finding",
            bind_to_agent=True,
        ),
        current_user=object(),
    )

    assert response.slug == "demo-skill"
    assert (skill_dir / "SKILL.md").read_text(encoding="utf-8") == original_content
    assert (skill_dir / "references" / "guide.md").read_text(encoding="utf-8") == "guide"
    binding = SkillFileService.get_agent_bindings("finding")["skills"][0]
    assert binding["slug"] == "demo-skill"
