from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Tuple

from .models import SkillEntry


def parse_frontmatter(content: str) -> Tuple[Dict[str, Any], str]:
    text = (content or "").replace("\r\n", "\n")
    if not text.startswith("---\n"):
        return {}, text
    end = text.find("\n---\n", 4)
    if end == -1:
        return {}, text

    raw_meta = text[4:end].strip()
    body = text[end + 5 :].lstrip()
    metadata: Dict[str, Any] = {}
    for line in raw_meta.splitlines():
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        key = key.strip().lower()
        value = value.strip()
        if not key:
            continue
        if value.startswith("[") and value.endswith("]"):
            metadata[key] = [item.strip().strip("'\"") for item in value[1:-1].split(",") if item.strip()]
        elif value.lower() in {"true", "false"}:
            metadata[key] = value.lower() == "true"
        else:
            metadata[key] = value.strip("'\"")
    return metadata, body


def _read_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return default


def _build_paths(base_dir: Path, project_root: Path, file_name: str = "SKILL.md") -> Dict[str, str]:
    relative = base_dir.resolve().relative_to(project_root.resolve()).as_posix()
    return {
        "storage_path": str(base_dir),
        "workspace_relative_path": relative,
        "workspace_file_path": f"{relative}/{file_name}",
        "skill_root": str(base_dir),
        "skill_file_path": str(base_dir / file_name),
        "references_root": str(base_dir / "references"),
        "examples_root": str(base_dir / "examples"),
        "scripts_root": str(base_dir / "scripts"),
    }


def _build_extension_manifest(skill_dir: Path) -> List[Dict[str, Any]]:
    manifest: List[Dict[str, Any]] = []
    for folder in ("references", "examples", "scripts"):
        base = skill_dir / folder
        if not base.exists():
            continue
        for file in sorted(base.rglob("*")):
            if file.is_dir():
                continue
            manifest.append(
                {
                    "name": file.relative_to(skill_dir).as_posix(),
                    "description": f"Local skill resource {file.name}",
                    "type": "file",
                }
            )
    return manifest


def discover_skill_entries(library_root: Path, project_root: Path | None = None) -> List[SkillEntry]:
    library_root = Path(library_root).resolve()
    project_root = Path(project_root).resolve() if project_root else library_root.parent.resolve()
    if not library_root.exists():
        return []

    entries: List[SkillEntry] = []
    for child in sorted(library_root.iterdir(), key=lambda item: item.name.lower()):
        if not child.is_dir():
            continue
        if child.name in {"agents", ".runtime"} or child.name.startswith("."):
            continue

        skill_file = child / "SKILL.md"
        if not skill_file.exists() or not skill_file.is_file():
            continue

        content = skill_file.read_text(encoding="utf-8")
        frontmatter, body = parse_frontmatter(content)
        raw_metadata = _read_json(child / "metadata.json", {})
        nested_metadata = raw_metadata.get("metadata", {}) if isinstance(raw_metadata, dict) else {}
        paths = _build_paths(child, project_root)

        tags = frontmatter.get("tags")
        if not isinstance(tags, list):
            tags = raw_metadata.get("tags") or nested_metadata.get("tags") or []
        if not isinstance(tags, list):
            tags = []

        metadata_json = {
            **(nested_metadata if isinstance(nested_metadata, dict) else {}),
            **paths,
            "workspace_skill_file": paths["workspace_file_path"],
            "folder_path": str(child),
            "bindings_file": str(child / "bindings.json"),
        }

        entries.append(
            SkillEntry(
                slug=child.name,
                name=str(frontmatter.get("name") or raw_metadata.get("name") or child.name),
                description=str(frontmatter.get("description") or raw_metadata.get("description") or ""),
                skill_file=str(skill_file),
                folder_path=str(child),
                tags=[str(tag) for tag in tags],
                frontmatter=frontmatter,
                metadata_json=metadata_json,
                source_type=str(raw_metadata.get("source_type") or nested_metadata.get("source_type") or "manual"),
                source_url=raw_metadata.get("source_url") or nested_metadata.get("source_url"),
                content=content,
                skill_body=body,
                extension_manifest=raw_metadata.get("extension_manifest") or _build_extension_manifest(child),
                is_system=bool(raw_metadata.get("is_system", False)),
                is_active=bool(raw_metadata.get("is_active", True)),
            )
        )

    return entries
