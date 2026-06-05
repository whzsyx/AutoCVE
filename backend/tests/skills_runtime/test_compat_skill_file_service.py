import io
import json
import zipfile

import pytest

from app.services.init_agent_assets import init_skill_bindings
import app.services.skill_file_service as skill_file_service_module
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

    installed_index = json.loads(
        (tmp_path / "skill_library" / ".runtime" / "installed_skills.json").read_text(encoding="utf-8")
    )

    assert skill["slug"] == "alpha"
    assert (tmp_path / "skill_library" / "alpha" / "SKILL.md").exists()
    assert not (tmp_path / "skill_library" / "agents" / "finding" / "alpha").exists()
    assert installed_index["skills"][0]["slug"] == "alpha"
    assert installed_index["skills"][0]["source_type"] == "manual"
    assert installed_index["skills"][0]["bound_agents"] == []
    assert installed_index["skills"][0]["skill_file"].replace("\\", "/").endswith("skill_library/alpha/SKILL.md")


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
    installed_index = json.loads(
        (tmp_path / "skill_library" / ".runtime" / "installed_skills.json").read_text(encoding="utf-8")
    )

    assert binding["skill_id"] == "alpha"
    assert aggregated["skills"][0]["skill_id"] == "alpha"
    assert aggregated["skills"][0]["skill_file"].replace("\\", "/").endswith("skill_library/alpha/SKILL.md")
    assert aggregated["skills"][0]["workspace_relative_path"] == "skill_library/alpha"
    assert not (tmp_path / "skill_library" / "agents" / "finding" / "alpha").exists()
    assert installed_index["skills"][0]["bound_agents"] == ["finding"]
    assert installed_index["skills"][0]["bindings"][0]["id"] == "finding:alpha"


def test_binding_update_refreshes_single_skill_runtime_without_full_sync(tmp_path, monkeypatch):
    monkeypatch.setattr(SkillFileService, "project_root", classmethod(lambda cls: tmp_path))

    SkillFileService.write_skill(
        slug="alpha",
        name="alpha",
        description="Alpha skill",
        content="# Alpha",
        tags=[],
    )
    SkillFileService.write_skill(
        slug="beta",
        name="beta",
        description="Beta skill",
        content="# Beta",
        tags=[],
    )
    SkillFileService.upsert_binding("finding", "alpha", enabled=True, always_include=True)

    def fail_full_sync(cls):
        raise AssertionError("binding updates should not run full sync_all")

    monkeypatch.setattr(SkillFileService, "sync_all", classmethod(fail_full_sync))

    updated = SkillFileService.update_binding("finding", "alpha", {"enabled": False})

    aggregated = json.loads((tmp_path / "skill_library" / "alpha" / "bindings.json").read_text(encoding="utf-8"))
    installed_index = json.loads(
        (tmp_path / "skill_library" / ".runtime" / "installed_skills.json").read_text(encoding="utf-8")
    )
    records = {item["slug"]: item for item in installed_index["skills"]}

    assert updated["enabled"] is False
    assert aggregated["skills"][0]["enabled"] is False
    assert records["alpha"]["bindings"][0]["enabled"] is False
    assert "beta" in records


def test_delete_skill_removes_directory_and_bindings_without_per_binding_refresh(tmp_path, monkeypatch):
    monkeypatch.setattr(SkillFileService, "project_root", classmethod(lambda cls: tmp_path))

    SkillFileService.write_skill(
        slug="alpha",
        name="alpha",
        description="Alpha skill",
        content="# Alpha",
        tags=[],
    )
    SkillFileService.write_skill(
        slug="beta",
        name="beta",
        description="Beta skill",
        content="# Beta",
        tags=[],
    )
    SkillFileService.upsert_binding("finding", "alpha", enabled=True)
    SkillFileService.upsert_binding("audit_chat", "alpha", enabled=True)
    SkillFileService.upsert_binding("finding", "beta", enabled=True)

    def fail_single_skill_refresh(cls, slug):
        raise AssertionError("delete_skill should not refresh runtime once per binding")

    monkeypatch.setattr(SkillFileService, "sync_skill_runtime", classmethod(fail_single_skill_refresh))

    SkillFileService.delete_skill("alpha")

    installed_index = json.loads(
        (tmp_path / "skill_library" / ".runtime" / "installed_skills.json").read_text(encoding="utf-8")
    )

    assert not (tmp_path / "skill_library" / "alpha").exists()
    assert (tmp_path / "skill_library" / "beta").exists()
    assert "alpha" not in SkillFileService.list_skill_slugs()
    assert [item["slug"] for item in SkillFileService.get_agent_bindings("finding")["skills"]] == ["beta"]
    assert SkillFileService.get_agent_bindings("audit_chat")["skills"] == []
    assert [item["slug"] for item in installed_index["skills"]] == ["beta"]


@pytest.mark.asyncio
async def test_audit_chat_agent_bindings_default_to_all_local_skills(tmp_path, monkeypatch):
    monkeypatch.setattr(SkillFileService, "project_root", classmethod(lambda cls: tmp_path))

    SkillFileService.write_skill(
        slug="alpha",
        name="alpha",
        description="Alpha skill",
        content="# Alpha",
        tags=[],
    )
    SkillFileService.write_skill(
        slug="beta",
        name="beta",
        description="Beta skill",
        content="# Beta",
        tags=[],
    )

    await init_skill_bindings()

    payload = SkillFileService.get_agent_bindings("audit_chat")
    assert payload["agent_type"] == "audit_chat"
    assert [item["slug"] for item in payload["skills"]] == ["alpha", "beta"]
    assert all(item["enabled"] for item in payload["skills"])


def _repo_zip_bytes(files: dict[str, str | bytes]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name, content in files.items():
            archive.writestr(name, content)
    return buffer.getvalue()


class _FakeGithubZipResponse:
    def __init__(self, content: bytes):
        self.content = content

    def raise_for_status(self) -> None:
        return None


class _FakeGithubZipClient:
    def __init__(self, expected_url: str, content: bytes):
        self.expected_url = expected_url
        self.content = content

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, traceback):
        return None

    async def get(self, url: str):
        assert url == self.expected_url
        return _FakeGithubZipResponse(self.content)


class _FakeGithubResponse:
    def __init__(self, *, content: bytes = b"", json_payload=None):
        self.content = content
        self._json_payload = json_payload or {}

    def raise_for_status(self) -> None:
        return None

    def json(self):
        return self._json_payload


class _FakeGithubSequenceClient:
    def __init__(self, responses: list[tuple[str, _FakeGithubResponse]]):
        self.responses = list(responses)

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, traceback):
        return None

    async def get(self, url: str):
        expected_url, response = self.responses.pop(0)
        assert url == expected_url
        return response


@pytest.mark.asyncio
async def test_import_github_skill_installs_complete_skill_directory(tmp_path, monkeypatch):
    monkeypatch.setattr(SkillFileService, "project_root", classmethod(lambda cls: tmp_path))
    archive = _repo_zip_bytes(
        {
            "repo-main/skills/demo-skill/SKILL.md": (
                "---\n"
                "name: Demo Skill\n"
                "description: Demo description\n"
                "tags: [github, audit]\n"
                "---\n\n"
                "# Demo\n"
            ),
            "repo-main/skills/demo-skill/references/guide.md": "guide",
            "repo-main/skills/demo-skill/scripts/check.py": "print('ok')\n",
        }
    )
    fake_client = _FakeGithubZipClient("https://codeload.github.com/example/repo/zip/main", archive)
    monkeypatch.setattr(skill_file_service_module.httpx, "AsyncClient", lambda **kwargs: fake_client)

    skill = await SkillFileService.import_github_skill(
        "https://github.com/example/repo/tree/main/skills/demo-skill"
    )

    assert skill["slug"] == "demo-skill"
    assert skill["name"] == "Demo Skill"
    assert skill["source_type"] == "github"
    assert (tmp_path / "skill_library" / "demo-skill" / "SKILL.md").exists()
    assert (tmp_path / "skill_library" / "demo-skill" / "references" / "guide.md").read_text(
        encoding="utf-8"
    ) == "guide"
    assert (tmp_path / "skill_library" / "demo-skill" / "scripts" / "check.py").exists()
    assert (tmp_path / "skill_library" / "demo-skill" / "bindings.json").exists()


@pytest.mark.asyncio
async def test_import_github_skill_installs_root_repo_skill_using_default_branch(tmp_path, monkeypatch):
    monkeypatch.setattr(SkillFileService, "project_root", classmethod(lambda cls: tmp_path))
    archive = _repo_zip_bytes(
        {
            "root-skill-trunk/SKILL.md": (
                "---\n"
                "name: Root Skill\n"
                "description: Root repo skill\n"
                "---\n\n"
                "# Root\n"
            ),
            "root-skill-trunk/references/root.md": "root guide",
        }
    )
    fake_client = _FakeGithubSequenceClient(
        [
            (
                "https://api.github.com/repos/example/root-skill",
                _FakeGithubResponse(json_payload={"default_branch": "trunk"}),
            ),
            (
                "https://codeload.github.com/example/root-skill/zip/trunk",
                _FakeGithubResponse(content=archive),
            ),
        ]
    )
    monkeypatch.setattr(skill_file_service_module.httpx, "AsyncClient", lambda **kwargs: fake_client)

    skill = await SkillFileService.import_github_skill("https://github.com/example/root-skill")

    assert skill["slug"] == "root-skill"
    assert skill["source_type"] == "github"
    assert (tmp_path / "skill_library" / "root-skill" / "references" / "root.md").read_text(
        encoding="utf-8"
    ) == "root guide"
    assert skill["metadata_json"]["branch"] == "trunk"


@pytest.mark.asyncio
async def test_import_github_skill_rejects_directory_without_skill_file(tmp_path, monkeypatch):
    monkeypatch.setattr(SkillFileService, "project_root", classmethod(lambda cls: tmp_path))
    archive = _repo_zip_bytes({"repo-main/not-a-skill/README.md": "# no skill\n"})
    fake_client = _FakeGithubZipClient("https://codeload.github.com/example/repo/zip/main", archive)
    monkeypatch.setattr(skill_file_service_module.httpx, "AsyncClient", lambda **kwargs: fake_client)

    with pytest.raises(ValueError, match="SKILL.md not found"):
        await SkillFileService.import_github_skill(
            "https://github.com/example/repo/tree/main/not-a-skill"
        )


@pytest.mark.asyncio
async def test_import_github_skill_rejects_zip_path_traversal(tmp_path, monkeypatch):
    monkeypatch.setattr(SkillFileService, "project_root", classmethod(lambda cls: tmp_path))
    archive = _repo_zip_bytes(
        {
            "repo-main/skills/demo-skill/SKILL.md": "# Demo\n",
            "../escape.txt": "bad",
        }
    )
    fake_client = _FakeGithubZipClient("https://codeload.github.com/example/repo/zip/main", archive)
    monkeypatch.setattr(skill_file_service_module.httpx, "AsyncClient", lambda **kwargs: fake_client)

    with pytest.raises(ValueError, match="outside"):
        await SkillFileService.import_github_skill(
            "https://github.com/example/repo/tree/main/skills/demo-skill"
        )
