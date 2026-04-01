from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Dict, List

from .finding_candidates import CandidateCase
from .finding_evidence import EvidenceBundle, EvidenceBundleStore


@dataclass
class WorkerResult:
    candidate_id: str
    status: str
    confirmed_findings: List[Dict[str, Any]] = field(default_factory=list)
    candidate_findings: List[Dict[str, Any]] = field(default_factory=list)
    coverage_delta: Dict[str, Any] = field(default_factory=dict)
    evidence_bundle_ids: List[str] = field(default_factory=list)
    retry_hint: str = ""


class CandidateWorker:
    EVIDENCE_ACTIONS = {"read_file", "function_context", "dataflow_analysis", "search_code"}

    def __init__(self, evidence_store: EvidenceBundleStore) -> None:
        self.evidence_store = evidence_store

    def build_worker_brief(self, candidate: CandidateCase) -> str:
        return (
            f"Audit candidate {candidate.id} ({candidate.vuln_family}) with focus on "
            f"entry points {candidate.entry_point_refs[:2]} and sinks {candidate.sink_refs[:2]}."
        )

    def record_tool_result(
        self,
        candidate: CandidateCase,
        action: str,
        action_input: Dict[str, Any],
        observation: str,
    ) -> WorkerResult:
        if action not in self.EVIDENCE_ACTIONS:
            return WorkerResult(candidate_id=candidate.id, status="needs_followup")

        file_path = self._extract_file_path(observation, action_input)
        if not file_path:
            return WorkerResult(
                candidate_id=candidate.id,
                status="needs_followup",
                retry_hint="No file path could be extracted from the observation.",
            )

        line_start, line_end = self._extract_line_range(observation, action_input)
        snippet = self._extract_snippet(observation)
        bundle_id = self._build_bundle_id(candidate.id, file_path, line_start, line_end, action)
        self.evidence_store.upsert(
            EvidenceBundle(
                id=bundle_id,
                file_path=file_path,
                line_start=line_start,
                line_end=line_end,
                snippet=snippet,
                source_desc=(candidate.source_refs[0] if candidate.source_refs else "attacker-controlled input under review"),
                sink_desc=(candidate.sink_refs[0] if candidate.sink_refs else f"{action} observation"),
                control_analysis=", ".join(candidate.control_refs) if candidate.control_refs else "control path under review",
                business_flow_analysis=", ".join(candidate.business_flow_notes),
                entry_point_refs=candidate.entry_point_refs.copy(),
                priority_path_refs=[file_path],
                evidence_gaps=[] if snippet else ["missing_code_snippet"],
                confidence=0.83 if action == "dataflow_analysis" else 0.78,
            )
        )
        if bundle_id not in candidate.evidence_bundle_ids:
            candidate.evidence_bundle_ids.append(bundle_id)

        return WorkerResult(
            candidate_id=candidate.id,
            status="candidate",
            coverage_delta={"file_paths": [file_path]},
            evidence_bundle_ids=[bundle_id],
        )

    def _extract_file_path(self, observation: str, action_input: Dict[str, Any]) -> str:
        text = str(observation or "")
        match = re.search(r"文件:\s*(.+)", text)
        if match:
            return match.group(1).strip()
        file_path = str(action_input.get("file_path", "") or action_input.get("path", "")).strip()
        return file_path

    def _extract_line_range(self, observation: str, action_input: Dict[str, Any]) -> tuple[int, int]:
        text = str(observation or "")
        match = re.search(r"行数:\s*(\d+)(?:-(\d+))?", text)
        if match:
            start = int(match.group(1))
            end = int(match.group(2) or start)
            return start, end
        start = int(action_input.get("start_line") or 1)
        end = int(action_input.get("end_line") or start)
        return start, end

    def _extract_snippet(self, observation: str) -> str:
        text = str(observation or "")
        match = re.search(r"```[a-zA-Z0-9_]*\n(.*?)```", text, re.DOTALL)
        if match:
            return match.group(1).strip()
        return text[:800].strip()

    def _build_bundle_id(self, candidate_id: str, file_path: str, line_start: int, line_end: int, action: str) -> str:
        normalized_path = re.sub(r"[^a-zA-Z0-9_]+", "_", file_path).strip("_")
        return f"{candidate_id}:{action}:{normalized_path}:{line_start}-{line_end}"
