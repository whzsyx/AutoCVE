from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Tuple


@dataclass
class CandidateCase:
    id: str
    vuln_family: str
    priority: int
    entry_point_refs: List[str] = field(default_factory=list)
    source_refs: List[str] = field(default_factory=list)
    sink_refs: List[str] = field(default_factory=list)
    control_refs: List[str] = field(default_factory=list)
    business_flow_notes: List[str] = field(default_factory=list)
    status: str = "new"
    confidence: float = 0.0
    evidence_bundle_ids: List[str] = field(default_factory=list)
    next_actions: List[Dict[str, object]] = field(default_factory=list)


class CandidateQueueManager:
    def build_queue(self, candidates: List[CandidateCase]) -> List[CandidateCase]:
        deduped: Dict[Tuple[object, ...], CandidateCase] = {}
        for candidate in sorted(candidates, key=lambda item: item.priority, reverse=True):
            deduped.setdefault(self._candidate_key(candidate), candidate)
        return list(deduped.values())

    def _candidate_key(self, candidate: CandidateCase) -> Tuple[object, ...]:
        return (
            candidate.vuln_family,
            tuple(sorted(set(candidate.entry_point_refs))),
            tuple(sorted(set(candidate.source_refs))),
            tuple(sorted(set(candidate.sink_refs))),
            tuple(sorted(set(candidate.control_refs))),
        )
