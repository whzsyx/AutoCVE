import json

from app.services.skills_runtime.discovery import discover_skill_entries


def test_discover_skill_entries_reads_canonical_skill_roots(tmp_path):
    project_root = tmp_path
    library_root = project_root / "skill_library"
    skill_dir = library_root / "demo-skill"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        "---\n"
        "name: demo-skill\n"
        "description: Demo description\n"
        "tags: [finding, auth]\n"
        "---\n\n"
        "# Demo\n",
        encoding="utf-8",
    )
    (skill_dir / "metadata.json").write_text(
        json.dumps({"source_type": "manual", "source_url": "https://example.com/demo"}),
        encoding="utf-8",
    )

    entries = discover_skill_entries(library_root=library_root, project_root=project_root)

    assert [entry.slug for entry in entries] == ["demo-skill"]
    entry = entries[0]
    assert entry.name == "demo-skill"
    assert entry.description == "Demo description"
    assert entry.tags == ["finding", "auth"]
    assert entry.frontmatter["name"] == "demo-skill"
    assert entry.skill_file.replace("\\", "/").endswith("skill_library/demo-skill/SKILL.md")
    assert entry.metadata_json["workspace_relative_path"] == "skill_library/demo-skill"
    assert entry.metadata_json["skill_file_path"].replace("\\", "/").endswith("skill_library/demo-skill/SKILL.md")
    assert entry.metadata_json["references_root"].replace("\\", "/").endswith("skill_library/demo-skill/references")


def test_discover_skill_entries_ignores_non_skill_directories(tmp_path):
    project_root = tmp_path
    library_root = project_root / "skill_library"
    (library_root / "agents").mkdir(parents=True)
    (library_root / ".runtime").mkdir(parents=True)
    (library_root / "empty-skill").mkdir(parents=True)

    entries = discover_skill_entries(library_root=library_root, project_root=project_root)

    assert entries == []
