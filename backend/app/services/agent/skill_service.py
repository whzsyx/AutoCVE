from __future__ import annotations

from typing import Any, Dict, List, Optional

from app.services.skill_file_service import SkillFileService
from app.services.skills_runtime.access import list_skill_resources, read_skill_body, read_skill_resource
from app.services.skills_runtime.catalog import resolve_agent_skill_state, resolve_skill_entry
from app.services.skills_runtime.migration import load_agent_bindings


class SkillService:
    @classmethod
    async def list_agent_skill_metadata(cls, user_id: Optional[str], agent_type: str) -> List[Dict[str, Any]]:
        del user_id
        library_root = SkillFileService.library_root()
        project_root = SkillFileService.project_root()
        state = resolve_agent_skill_state(
            library_root=library_root,
            project_root=project_root,
            agent_type=agent_type,
            context={},
        )
        bindings_by_slug = {
            binding.slug: binding
            for binding in load_agent_bindings(library_root=library_root, agent_type=agent_type)
        }
        items: List[Dict[str, Any]] = []
        for skill in state.entries:
            binding = bindings_by_slug.get(skill.slug)
            items.append(
                {
                    "id": skill.slug,
                    "name": skill.name,
                    "slug": skill.slug,
                    "description": skill.description,
                    "tags": skill.tags,
                    "source_type": skill.source_type,
                    "source_url": skill.source_url,
                    "always_include": bool(binding.always_include) if binding else False,
                    "match_keywords": list(binding.match_keywords) if binding else [],
                    "binding_id": f"{agent_type}:{skill.slug}",
                    "skill_metadata": {
                        "name": skill.name,
                        "description": skill.description,
                        "tags": skill.tags,
                        "frontmatter": skill.frontmatter,
                    },
                    "paths": {
                        "folder_path": skill.folder_path,
                        "skill_file": skill.skill_file,
                        "skill_root": skill.metadata_json.get("skill_root", skill.folder_path),
                        "skill_file_path": skill.metadata_json.get("skill_file_path", skill.skill_file),
                        "references_root": skill.metadata_json.get("references_root"),
                        "examples_root": skill.metadata_json.get("examples_root"),
                        "scripts_root": skill.metadata_json.get("scripts_root"),
                        "bindings_file": skill.metadata_json.get("bindings_file"),
                        **(skill.metadata_json or {}),
                    },
                    "extension_manifest": skill.extension_manifest,
                }
            )
        return items

    @classmethod
    async def resolve_agent_skills(cls, user_id: Optional[str], agent_type: str, context: Dict[str, Any]) -> Dict[str, Any]:
        del user_id
        state = resolve_agent_skill_state(
            library_root=SkillFileService.library_root(),
            project_root=SkillFileService.project_root(),
            agent_type=agent_type,
            context=context,
        )
        metadata = await cls.list_agent_skill_metadata(None, agent_type)
        matched = []
        for entry in state.matched:
            matched.append(
                {
                    "id": entry.slug,
                    "name": entry.name,
                    "slug": entry.slug,
                    "description": entry.description,
                    "source_type": entry.source_type,
                    "source_url": entry.source_url,
                    "extension_manifest": entry.extension_manifest,
                    "skill_metadata": {
                        "name": entry.name,
                        "description": entry.description,
                        "tags": entry.tags,
                        "frontmatter": entry.frontmatter,
                    },
                    "paths": {
                        "folder_path": entry.folder_path,
                        "skill_file": entry.skill_file,
                        "skill_root": entry.metadata_json.get("skill_root", entry.folder_path),
                        "skill_file_path": entry.metadata_json.get("skill_file_path", entry.skill_file),
                        "references_root": entry.metadata_json.get("references_root"),
                        "examples_root": entry.metadata_json.get("examples_root"),
                        "scripts_root": entry.metadata_json.get("scripts_root"),
                        **(entry.metadata_json or {}),
                    },
                    "matched_by": "runtime",
                }
            )
        route_plan = {
            "primary_skill": state.route_plan.primary_skill,
            "secondary_skills": list(state.route_plan.secondary_skills),
            "mandatory_reads": list(state.route_plan.mandatory_reads),
            "recommended_reads": list(state.route_plan.recommended_reads),
            "selection_reason": list(state.route_plan.selection_reason),
        }
        return {"metadata": metadata, "matched": matched, "prompt": state.prompt, "route_plan": route_plan}

    @staticmethod
    def build_skill_briefing(skill_context: Dict[str, Any]) -> str:
        prompt = (skill_context.get("prompt") or "").strip()
        route_plan = skill_context.get("route_plan") or {}
        if not prompt:
            return ""

        lines = [
            "Skills runtime catalog:",
            "Startup metadata is only a catalog. Read the actual SKILL.md before relying on a skill.",
            prompt,
        ]
        primary_skill = route_plan.get("primary_skill")
        if primary_skill:
            lines.extend(["", f"Primary skill: {primary_skill}"])
        secondary = route_plan.get("secondary_skills") or []
        if secondary:
            lines.append(f"Secondary skills: {', '.join(secondary)}")
        return "\n".join(lines)

    @classmethod
    def _find_skill(cls, skill_ref: str, agent_type: Optional[str] = None):
        return resolve_skill_entry(
            library_root=SkillFileService.library_root(),
            project_root=SkillFileService.project_root(),
            skill_ref=skill_ref,
            agent_type=agent_type,
        )

    @classmethod
    async def get_skill_body(cls, user_id: Optional[str], skill_ref: str, agent_type: Optional[str] = None) -> Dict[str, Any]:
        del user_id
        entry = cls._find_skill(skill_ref, agent_type=agent_type)
        return read_skill_body(entry)

    @classmethod
    async def get_skill_resource(
        cls,
        user_id: Optional[str],
        skill_ref: str,
        resource_name: str,
        agent_type: Optional[str] = None,
    ) -> Dict[str, Any]:
        del user_id
        entry = cls._find_skill(skill_ref, agent_type=agent_type)
        return read_skill_resource(entry, resource_name)

    @classmethod
    async def list_skill_resources(
        cls,
        user_id: Optional[str],
        skill_ref: str,
        resource_name: str = "",
        agent_type: Optional[str] = None,
    ) -> Dict[str, Any]:
        del user_id
        entry = cls._find_skill(skill_ref, agent_type=agent_type)
        return list_skill_resources(entry, resource_name)

    @classmethod
    async def import_github_skill(cls, repo_url: str) -> Dict[str, Any]:
        return await SkillFileService.import_github_skill(repo_url)

    @staticmethod
    def tool_usage_guide() -> str:
        return (
            "Skills runtime:\n"
            "- Prefer generic file tools with catalog paths: read_file(skill_file_path), list_files(references_root), and read_many_files([...]).\n"
            "- Use skill materials just in time; do not exhaust the whole reference tree before auditing project code.\n"
            "- Start with SKILL.md, read only the next concrete reference you need, then return to source code."
        )
