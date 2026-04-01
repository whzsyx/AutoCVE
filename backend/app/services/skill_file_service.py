from __future__ import annotations

import json
import os
import re
import shutil
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

import httpx

from app.services.skills_runtime.discovery import parse_frontmatter

AGENT_TYPES = ["orchestrator", "recon", "scan", "triage", "finding", "verification"]


def _safe_relative_path(value: str) -> str:
    normalized = value.replace("\\", "/").strip("/")
    parts = [part for part in normalized.split("/") if part not in {"", ".", ".."}]
    return "/".join(parts)


class SkillFileService:
    """Filesystem-first skill catalog and binding management."""

    @classmethod
    def project_root(cls) -> Path:
        env_root = os.getenv("AUDITAI_ASSET_ROOT", "").strip() or os.getenv("DEEPAUDIT_ASSET_ROOT", "").strip()
        if env_root:
            return Path(env_root)

        current = Path(__file__).resolve()
        best_candidate: Optional[Path] = None
        best_score = -1
        for candidate in current.parents:
            if (candidate / "docker-compose.yml").exists():
                score = 3
            elif (candidate / "skill_library").exists() or (candidate / "report_template_library").exists():
                score = 2
            elif (candidate / "alembic.ini").exists() and (candidate / "app").exists():
                score = 1
            else:
                score = -1
            if score > best_score:
                best_candidate = candidate
                best_score = score
                if score == 3:
                    break
        return best_candidate or current.parents[2]

    @classmethod
    def library_root(cls) -> Path:
        root = cls.project_root() / "skill_library"
        root.mkdir(parents=True, exist_ok=True)
        return root

    @classmethod
    def agents_root(cls) -> Path:
        root = cls.library_root() / "agents"
        root.mkdir(parents=True, exist_ok=True)
        return root

    @classmethod
    def display_root(cls) -> str:
        return "skill_library"

    @classmethod
    def _host_project_root(cls) -> str:
        return os.getenv("HOST_PROJECT_ROOT", "").strip().rstrip("/\\")

    @classmethod
    def _to_host_path(cls, relative_path: str) -> str:
        host_root = cls._host_project_root()
        if not host_root:
            return ""
        normalized = relative_path.replace("/", "\\")
        return f"{host_root}\\{normalized}" if normalized else host_root

    @classmethod
    def slugify(cls, value: str) -> str:
        slug = re.sub(r"[^a-zA-Z0-9._-]+", "-", (value or "").strip().lower()).strip("-")
        return slug or "skill"

    @classmethod
    def skill_dir(cls, slug: str) -> Path:
        path = cls.library_root() / cls.slugify(slug)
        path.mkdir(parents=True, exist_ok=True)
        return path

    @classmethod
    def skill_file(cls, slug: str) -> Path:
        return cls.skill_dir(slug) / "SKILL.md"

    @classmethod
    def metadata_file(cls, slug: str) -> Path:
        return cls.skill_dir(slug) / "metadata.json"

    @classmethod
    def aggregated_bindings_file(cls, slug: str) -> Path:
        return cls.skill_dir(slug) / "bindings.json"

    @classmethod
    def agent_root(cls, agent_type: str) -> Path:
        path = cls.agents_root() / agent_type
        path.mkdir(parents=True, exist_ok=True)
        return path

    @classmethod
    def bindings_file(cls, agent_type: str) -> Path:
        return cls.agent_root(agent_type) / "bindings.json"

    @classmethod
    def agent_skill_dir(cls, agent_type: str, slug: str) -> Path:
        path = cls.agent_root(agent_type) / cls.slugify(slug)
        path.mkdir(parents=True, exist_ok=True)
        return path

    @classmethod
    def _build_paths(cls, base_dir: Path, file_name: str = "SKILL.md") -> Dict[str, str]:
        relative = base_dir.relative_to(cls.project_root()).as_posix()
        return {
            "storage_path": str(base_dir),
            "workspace_relative_path": relative,
            "workspace_file_path": f"{relative}/{file_name}",
            "workspace_skill_file": f"{relative}/{file_name}",
            "host_storage_path": cls._to_host_path(relative),
            "host_file_path": cls._to_host_path(f"{relative}/{file_name}"),
            "skill_root": str(base_dir),
            "skill_file_path": str(base_dir / file_name),
            "references_root": str(base_dir / "references"),
            "examples_root": str(base_dir / "examples"),
            "scripts_root": str(base_dir / "scripts"),
            "display_root": cls.display_root(),
        }

    @classmethod
    def _build_extension_manifest(cls, skill_dir: Path) -> List[Dict[str, Any]]:
        manifest: List[Dict[str, Any]] = []
        for folder in ("references", "examples", "scripts"):
            base = skill_dir / folder
            if not base.exists():
                continue
            for file in sorted(base.rglob("*")):
                if file.is_dir():
                    continue
                logical_name = file.relative_to(skill_dir).as_posix()
                manifest.append({"name": logical_name, "description": f"Imported from {logical_name}", "type": "file"})
        return manifest

    @classmethod
    def _read_json(cls, path: Path, default: Any) -> Any:
        if not path.exists():
            return default
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return default

    @classmethod
    def _write_json(cls, path: Path, payload: Any) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    @classmethod
    def _frontmatter_block(cls, metadata: Dict[str, Any]) -> str:
        lines = ["---"]
        for key in ("name", "description", "tags"):
            value = metadata.get(key)
            if value is None:
                continue
            if isinstance(value, list):
                serialized = ", ".join(json.dumps(item, ensure_ascii=False) for item in value)
                lines.append(f"{key}: [{serialized}]")
            else:
                lines.append(f"{key}: {json.dumps(str(value), ensure_ascii=False)}")
        lines.append("---")
        return "\n".join(lines)

    @classmethod
    def _normalize_binding(
        cls,
        agent_type: str,
        slug: str,
        *,
        enabled: bool = True,
        always_include: bool = False,
        sort_order: int = 0,
        match_keywords: Optional[List[str]] = None,
        match_config: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        normalized_slug = cls.slugify(slug)
        return {
            "id": f"{agent_type}:{normalized_slug}",
            "skill_id": normalized_slug,
            "slug": normalized_slug,
            "agent_type": agent_type,
            "enabled": bool(enabled),
            "always_include": bool(always_include),
            "sort_order": int(sort_order),
            "match_keywords": [str(item) for item in (match_keywords or []) if str(item).strip()],
            "match_config": match_config or {},
            "bindings_file": str(cls.bindings_file(agent_type)),
            "skill_file": str(cls.skill_file(normalized_slug)),
        }

    @classmethod
    def _write_agent_binding_mirror(cls, binding: Dict[str, Any]) -> None:
        mirror_dir = cls.agent_skill_dir(binding["agent_type"], binding["slug"])
        cls._write_json(mirror_dir / "binding.json", binding)

    @classmethod
    def _remove_agent_binding_mirror(cls, agent_type: str, slug: str) -> None:
        mirror_dir = cls.agent_root(agent_type) / cls.slugify(slug)
        if mirror_dir.exists():
            shutil.rmtree(mirror_dir)

    @classmethod
    def _collect_bindings_for_slug(cls, slug: str) -> List[Dict[str, Any]]:
        normalized_slug = cls.slugify(slug)
        items: List[Dict[str, Any]] = []
        for agent_type in AGENT_TYPES:
            payload = cls.get_agent_bindings(agent_type)
            for binding in payload.get("skills", []):
                if cls.slugify(binding.get("slug", "")) == normalized_slug:
                    items.append(binding)
        return sorted(items, key=lambda item: (item["agent_type"], item["sort_order"], item["slug"]))

    @classmethod
    def _read_skill_payload(cls, slug: str) -> Dict[str, Any]:
        normalized_slug = cls.slugify(slug)
        skill_dir = cls.library_root() / normalized_slug
        skill_file = skill_dir / "SKILL.md"
        if not skill_file.exists():
            raise FileNotFoundError(f"Skill '{normalized_slug}' not found")

        content = skill_file.read_text(encoding="utf-8")
        frontmatter, body = parse_frontmatter(content)
        raw_metadata = cls._read_json(cls.metadata_file(normalized_slug), {})
        nested_metadata = raw_metadata.get("metadata", {}) if isinstance(raw_metadata, dict) else {}
        paths = cls._build_paths(skill_dir)
        extension_manifest = raw_metadata.get("extension_manifest") or cls._build_extension_manifest(skill_dir)
        metadata_json = {
            **(nested_metadata if isinstance(nested_metadata, dict) else {}),
            **paths,
            "folder_path": str(skill_dir),
            "bindings_file": str(cls.aggregated_bindings_file(normalized_slug)),
        }
        name = str(frontmatter.get("name") or raw_metadata.get("name") or normalized_slug)
        description = str(frontmatter.get("description") or raw_metadata.get("description") or "")
        tags = frontmatter.get("tags")
        if not isinstance(tags, list):
            tags = raw_metadata.get("tags", [])
        if not isinstance(tags, list):
            tags = []

        return {
            "id": normalized_slug,
            "name": name,
            "slug": normalized_slug,
            "description": description,
            "tags": [str(tag) for tag in tags],
            "source_type": str(raw_metadata.get("source_type", "manual")),
            "source_url": raw_metadata.get("source_url"),
            "metadata_json": metadata_json,
            "is_system": bool(raw_metadata.get("is_system", False)),
            "is_active": bool(raw_metadata.get("is_active", True)),
            "bindings": cls._collect_bindings_for_slug(normalized_slug),
            "folder_path": str(skill_dir),
            "skill_file": str(skill_file),
            "bindings_file": str(cls.aggregated_bindings_file(normalized_slug)),
            "content": content,
            "skill_body": body,
            "extension_manifest": extension_manifest,
            "extension_payload": raw_metadata.get("extension_payload", {}) or {},
        }

    @classmethod
    def ensure_agent_roots(cls) -> None:
        for agent in AGENT_TYPES:
            cls.ensure_agent_bindings(agent)

    @classmethod
    def ensure_agent_bindings(cls, agent_type: str) -> None:
        payload = cls._read_json(cls.bindings_file(agent_type), None)
        if not isinstance(payload, dict):
            payload = None
        if payload is None:
            cls._write_json(cls.bindings_file(agent_type), {"agent_type": agent_type, "skills": []})

    @classmethod
    def list_skill_slugs(cls) -> List[str]:
        slugs: List[str] = []
        for entry in sorted(cls.library_root().iterdir(), key=lambda item: item.name.lower()):
            if entry.is_dir() and entry.name not in {"agents", ".runtime"} and not entry.name.startswith(".") and (entry / "SKILL.md").exists():
                slugs.append(entry.name)
        return slugs

    @classmethod
    def list_skills(cls) -> List[Dict[str, Any]]:
        return [cls._read_skill_payload(slug) for slug in cls.list_skill_slugs()]

    @classmethod
    def read_skill(cls, slug: str) -> Dict[str, Any]:
        return cls._read_skill_payload(slug)

    @classmethod
    def write_skill(
        cls,
        *,
        slug: str,
        name: str,
        description: Optional[str],
        content: Optional[str],
        tags: Optional[List[str]],
        source_type: str,
        source_url: Optional[str],
        metadata_json: Optional[Dict[str, Any]],
        is_system: bool,
        is_active: bool,
        extension_payload: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        normalized_slug = cls.slugify(slug)
        skill_dir = cls.skill_dir(normalized_slug)
        frontmatter = {
            "name": name,
            "description": description or "",
            "tags": list(tags or []),
        }
        body = (content or "").strip()
        skill_text = f"{cls._frontmatter_block(frontmatter)}\n\n{body}\n"
        cls.skill_file(normalized_slug).write_text(skill_text, encoding="utf-8")

        payload = {
            "name": name,
            "slug": normalized_slug,
            "description": description or "",
            "tags": list(tags or []),
            "source_type": source_type or "manual",
            "source_url": source_url,
            "metadata": metadata_json or {},
            "extension_manifest": cls._build_extension_manifest(skill_dir),
            "extension_payload": extension_payload or {},
            "is_system": bool(is_system),
            "is_active": bool(is_active),
        }
        cls._write_json(cls.metadata_file(normalized_slug), payload)
        cls.sync_all()
        return cls.read_skill(normalized_slug)

    @classmethod
    def rename_skill(cls, current_slug: str, new_slug: str) -> Dict[str, Any]:
        current_slug = cls.slugify(current_slug)
        new_slug = cls.slugify(new_slug)
        if current_slug == new_slug:
            return cls.read_skill(new_slug)
        current_dir = cls.library_root() / current_slug
        if not current_dir.exists():
            raise FileNotFoundError(f"Skill '{current_slug}' not found")
        target_dir = cls.library_root() / new_slug
        if target_dir.exists():
            raise FileExistsError(f"Skill '{new_slug}' already exists")

        current_dir.rename(target_dir)
        metadata = cls._read_json(target_dir / "metadata.json", {})
        if isinstance(metadata, dict):
            metadata["slug"] = new_slug
            cls._write_json(target_dir / "metadata.json", metadata)

        for agent_type in AGENT_TYPES:
            payload = cls.get_agent_bindings(agent_type)
            changed = False
            for item in payload.get("skills", []):
                if cls.slugify(item.get("slug", "")) == current_slug:
                    item["slug"] = new_slug
                    item["skill_id"] = new_slug
                    item["id"] = f"{agent_type}:{new_slug}"
                    item["skill_file"] = str(cls.skill_file(new_slug))
                    changed = True
            if changed:
                cls._write_json(cls.bindings_file(agent_type), payload)
            mirror_dir = cls.agent_root(agent_type) / current_slug
            if mirror_dir.exists():
                mirror_dir.rename(cls.agent_root(agent_type) / new_slug)
                binding_file = cls.agent_root(agent_type) / new_slug / "binding.json"
                binding_payload = cls._read_json(binding_file, {})
                if isinstance(binding_payload, dict):
                    binding_payload["slug"] = new_slug
                    binding_payload["skill_id"] = new_slug
                    binding_payload["id"] = f"{agent_type}:{new_slug}"
                    binding_payload["skill_file"] = str(cls.skill_file(new_slug))
                    cls._write_json(binding_file, binding_payload)

        cls.sync_all()
        return cls.read_skill(new_slug)

    @classmethod
    def delete_skill(cls, slug: str) -> None:
        normalized_slug = cls.slugify(slug)
        skill_dir = cls.library_root() / normalized_slug
        if skill_dir.exists():
            shutil.rmtree(skill_dir)
        for agent_type in AGENT_TYPES:
            cls.delete_binding(agent_type, normalized_slug, ignore_missing=True)

    @classmethod
    def get_agent_bindings(cls, agent_type: str) -> Dict[str, Any]:
        cls.ensure_agent_bindings(agent_type)
        payload = cls._read_json(cls.bindings_file(agent_type), {"agent_type": agent_type, "skills": []})
        skills: List[Dict[str, Any]] = []
        for item in payload.get("skills", []) if isinstance(payload, dict) else []:
            skills.append(
                cls._normalize_binding(
                    agent_type,
                    item.get("slug", ""),
                    enabled=bool(item.get("enabled", True)),
                    always_include=bool(item.get("always_include", False)),
                    sort_order=int(item.get("sort_order", 0)),
                    match_keywords=item.get("match_keywords", []),
                    match_config=item.get("match_config", {}),
                )
            )
        return {"agent_type": agent_type, "skills": sorted(skills, key=lambda item: (item["sort_order"], item["slug"]))}

    @classmethod
    def upsert_binding(
        cls,
        agent_type: str,
        slug: str,
        *,
        enabled: bool = True,
        always_include: bool = False,
        sort_order: int = 0,
        match_keywords: Optional[List[str]] = None,
        match_config: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        normalized_slug = cls.slugify(slug)
        payload = cls.get_agent_bindings(agent_type)
        binding = cls._normalize_binding(
            agent_type,
            normalized_slug,
            enabled=enabled,
            always_include=always_include,
            sort_order=sort_order,
            match_keywords=match_keywords,
            match_config=match_config,
        )
        replaced = False
        for index, item in enumerate(payload["skills"]):
            if item["slug"] == normalized_slug:
                payload["skills"][index] = binding
                replaced = True
                break
        if not replaced:
            payload["skills"].append(binding)
        payload["skills"] = sorted(payload["skills"], key=lambda item: (item["sort_order"], item["slug"]))
        cls._write_json(cls.bindings_file(agent_type), payload)
        cls._write_agent_binding_mirror(binding)
        cls.sync_all()
        return binding

    @classmethod
    def update_binding(cls, agent_type: str, slug: str, updates: Dict[str, Any]) -> Dict[str, Any]:
        current = next((item for item in cls.get_agent_bindings(agent_type)["skills"] if item["slug"] == cls.slugify(slug)), None)
        if current is None:
            raise FileNotFoundError(f"Binding '{agent_type}:{slug}' not found")
        return cls.upsert_binding(
            agent_type,
            slug,
            enabled=bool(updates.get("enabled", current["enabled"])),
            always_include=bool(updates.get("always_include", current["always_include"])),
            sort_order=int(updates.get("sort_order", current["sort_order"])),
            match_keywords=updates.get("match_keywords", current.get("match_keywords", [])),
            match_config=updates.get("match_config", current.get("match_config", {})),
        )

    @classmethod
    def delete_binding(cls, agent_type: str, slug: str, *, ignore_missing: bool = False) -> None:
        normalized_slug = cls.slugify(slug)
        payload = cls.get_agent_bindings(agent_type)
        new_items = [item for item in payload["skills"] if item["slug"] != normalized_slug]
        if len(new_items) == len(payload["skills"]) and not ignore_missing:
            raise FileNotFoundError(f"Binding '{agent_type}:{normalized_slug}' not found")
        payload["skills"] = new_items
        cls._write_json(cls.bindings_file(agent_type), payload)
        cls._remove_agent_binding_mirror(agent_type, normalized_slug)
        cls.sync_all()

    @classmethod
    def sync_all(cls) -> None:
        cls.ensure_agent_roots()
        for slug in cls.list_skill_slugs():
            cls._write_json(cls.aggregated_bindings_file(slug), {"skills": cls._collect_bindings_for_slug(slug)})

    @classmethod
    async def import_github_skill(cls, repo_url: str) -> Dict[str, Any]:
        parsed = urlparse(repo_url)
        if parsed.netloc not in {"github.com", "www.github.com", "raw.githubusercontent.com"}:
            raise ValueError("Only GitHub URLs are supported")

        raw_url = repo_url
        if parsed.netloc in {"github.com", "www.github.com"}:
            parts = [part for part in parsed.path.strip("/").split("/") if part]
            if len(parts) < 5 or parts[2] not in {"tree", "blob"}:
                raise ValueError("GitHub skill URL must point to a tree/blob path")
            owner, repo, _, branch = parts[:4]
            subpath = "/".join(parts[4:])
            if not subpath.endswith("SKILL.md"):
                subpath = f"{subpath.rstrip('/')}/SKILL.md"
            raw_url = f"https://raw.githubusercontent.com/{owner}/{repo}/{branch}/{subpath}"

        async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as client:
            response = await client.get(raw_url)
            response.raise_for_status()
            content = response.text

        frontmatter, body = parse_frontmatter(content)
        slug = cls.slugify(frontmatter.get("name") or Path(raw_url).parent.name or "imported-skill")
        return {
            "slug": slug,
            "name": str(frontmatter.get("name") or slug),
            "description": str(frontmatter.get("description") or ""),
            "content": body,
            "tags": frontmatter.get("tags") if isinstance(frontmatter.get("tags"), list) else [],
            "source_type": "github",
            "source_url": repo_url,
            "metadata_json": {"imported_from": repo_url},
            "extension_payload": {},
        }
