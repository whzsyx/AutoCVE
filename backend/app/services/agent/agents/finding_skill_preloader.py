from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

@dataclass
class PreloadedSkillContext:
    primary_skill_name: str = ""
    primary_skill_body: Dict[str, Any] = field(default_factory=dict)
    mandatory_resources: List[Dict[str, Any]] = field(default_factory=list)
    recommended_resources: List[Dict[str, Any]] = field(default_factory=list)
    case_hints: List[str] = field(default_factory=list)
    compressed_guidance: str = ""

    def to_prompt_block(self) -> str:
        return ""


class FindingSkillPreloader:
    async def preload(self, user_id: Optional[str], context: Dict[str, Any]) -> PreloadedSkillContext:
        del user_id
        skill_context = context.get("skill_context", {}) or {}
        route_plan = skill_context.get("route_plan", {}) or {}
        primary_skill = str(route_plan.get("primary_skill", "") or "").strip()
        case_hints = [
            str(item).strip()
            for item in route_plan.get("case_candidates", []) or []
            if str(item).strip()
        ]

        loaded = PreloadedSkillContext(
            primary_skill_name=primary_skill,
            case_hints=case_hints,
        )
        return loaded

    def _normalize_resource_list(self, values: Any) -> List[str]:
        if not isinstance(values, list):
            return []
        return [str(item).strip() for item in values if str(item).strip()]
