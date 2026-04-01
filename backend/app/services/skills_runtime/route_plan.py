from __future__ import annotations

from .models import SkillEntry, SkillRoutePlan


def build_skill_route_plan(
    available: list[SkillEntry],
    matched: list[SkillEntry],
) -> SkillRoutePlan:
    if matched:
        primary = matched[0].slug
        secondary = [entry.slug for entry in matched[1:]]
        reasons = [f"Primary skill selected from matched runtime skills: {primary}"]
        if secondary:
            reasons.append("Additional matched skills available for targeted follow-up reads.")
        return SkillRoutePlan(
            primary_skill=primary,
            secondary_skills=secondary,
            selection_reason=reasons,
        )

    if available:
        primary = available[0].slug
        return SkillRoutePlan(
            primary_skill=primary,
            secondary_skills=[],
            selection_reason=[f"Fallback to first enabled skill: {primary}"],
        )

    return SkillRoutePlan()
