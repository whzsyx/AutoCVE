from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional


@dataclass
class EvidenceBundle:
    id: str
    file_path: str
    line_start: int
    line_end: int
    snippet: str
    source_desc: str
    sink_desc: str
    control_analysis: str
    business_flow_analysis: str
    entry_point_refs: List[str] = field(default_factory=list)
    priority_path_refs: List[str] = field(default_factory=list)
    evidence_gaps: List[str] = field(default_factory=list)
    confidence: float = 0.0


class EvidenceBundleStore:
    def __init__(self) -> None:
        self._bundles: Dict[str, EvidenceBundle] = {}

    def upsert(self, bundle: EvidenceBundle) -> str:
        self._bundles[bundle.id] = bundle
        return bundle.id

    def get(self, bundle_id: str) -> Optional[EvidenceBundle]:
        return self._bundles.get(bundle_id)

    def build_candidate_summary(self, bundle_ids: List[str]) -> Dict[str, object]:
        bundles = [self._bundles[bundle_id] for bundle_id in bundle_ids if bundle_id in self._bundles]
        return {
            "bundle_count": len(bundles),
            "file_paths": self._ordered_unique(bundle.file_path for bundle in bundles if bundle.file_path),
            "entry_point_refs": self._ordered_unique(
                ref
                for bundle in bundles
                for ref in bundle.entry_point_refs
                if ref
            ),
            "priority_path_refs": self._ordered_unique(
                ref
                for bundle in bundles
                for ref in bundle.priority_path_refs
                if ref
            ),
            "evidence_gaps": self._ordered_unique(
                gap
                for bundle in bundles
                for gap in bundle.evidence_gaps
                if gap
            ),
            "max_confidence": max((bundle.confidence for bundle in bundles), default=0.0),
        }

    def _ordered_unique(self, values) -> List[str]:
        seen = set()
        ordered: List[str] = []
        for value in values:
            if value in seen:
                continue
            seen.add(value)
            ordered.append(value)
        return ordered
