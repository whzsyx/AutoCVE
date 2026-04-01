from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Set


@dataclass
class CoverageMap:
    entry_points: List[str] = field(default_factory=list)
    sensitive_sinks: List[str] = field(default_factory=list)
    authz_paths: List[str] = field(default_factory=list)
    state_change_flows: List[str] = field(default_factory=list)
    covered_files: Set[str] = field(default_factory=set)
    covered_paths: Set[str] = field(default_factory=set)
    uncovered_priority_paths: List[str] = field(default_factory=list)
    repeated_queries: Dict[str, int] = field(default_factory=dict)


class CoverageBuilder:
    AUTHZ_HINTS = ("auth", "permission", "tenant", "role", "policy", "access")
    STATE_CHANGE_HINTS = ("create", "update", "delete", "approve", "upload", "payment", "order")
    SINK_HINTS = ("exec", "query", "render", "open", "request", "http", "sql")

    def build(self, context: Dict[str, object]) -> CoverageMap:
        recon_data = context.get("recon_data", {}) or {}
        entry_points = self._extract_entry_points(recon_data.get("entry_points", []))
        priority_paths = self._ordered_unique(recon_data.get("priority_paths", []) or recon_data.get("high_risk_areas", []) or [])

        coverage = CoverageMap(
            entry_points=entry_points,
            uncovered_priority_paths=priority_paths,
        )
        coverage.authz_paths = [path for path in priority_paths if self._contains_hint(path, self.AUTHZ_HINTS)]
        coverage.state_change_flows = [path for path in priority_paths if self._contains_hint(path, self.STATE_CHANGE_HINTS)]
        coverage.sensitive_sinks = [path for path in priority_paths if self._contains_hint(path, self.SINK_HINTS)]
        return coverage

    def _extract_entry_points(self, values: List[object]) -> List[str]:
        refs: List[str] = []
        for item in values:
            if isinstance(item, dict):
                file_path = str(item.get("file", "") or "").strip()
                line = item.get("line")
                if file_path and line:
                    refs.append(f"{file_path}:{line}")
                elif file_path:
                    refs.append(file_path)
            elif isinstance(item, str) and item.strip():
                refs.append(item.strip())
        return self._ordered_unique(refs)

    def _contains_hint(self, value: str, hints: tuple[str, ...]) -> bool:
        lowered = value.lower()
        return any(hint in lowered for hint in hints)

    def _ordered_unique(self, values: List[object]) -> List[str]:
        seen = set()
        ordered: List[str] = []
        for value in values:
            text = str(value).strip()
            if not text or text in seen:
                continue
            seen.add(text)
            ordered.append(text)
        return ordered
