from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from .finding_candidates import CandidateCase, CandidateQueueManager
from .finding_coverage import CoverageBuilder, CoverageMap


@dataclass
class AuditPlan:
    strategy: str = "coverage_first"
    focus_vulnerabilities: List[str] = field(default_factory=list)
    target_files: List[str] = field(default_factory=list)
    controller_budget: int = 12
    worker_budget: int = 10
    followup_worker_budget: int = 4
    max_followup_rounds_per_candidate: int = 2
    max_active_candidates: int = 8
    stop_conditions: List[str] = field(default_factory=list)


@dataclass
class FindingRuntimeState:
    plan: AuditPlan
    coverage: CoverageMap
    queue: List[CandidateCase] = field(default_factory=list)
    worker_sessions: Dict[str, "WorkerSession"] = field(default_factory=dict)
    active_candidate_id: Optional[str] = None
    phase: str = "evidence_collection"
    phase_reason: str = ""
    rotation_history: List[Dict[str, str]] = field(default_factory=list)
    findings: List[Dict[str, Any]] = field(default_factory=list)
    discarded_candidates: List[Dict[str, Any]] = field(default_factory=list)
    unresolved_candidates: List[Dict[str, Any]] = field(default_factory=list)
    metrics: Dict[str, Any] = field(default_factory=dict)


@dataclass
class WorkerSession:
    candidate_id: str
    brief: str
    max_budget: int
    remaining_budget: int
    status: str = "pending"
    rotation_reason: str = ""
    actions_taken: int = 0
    followup_rounds_left: int = 0
    message_history: List[Dict[str, str]] = field(default_factory=list)


class FindingController:
    DEFAULT_VULN_FAMILIES = ["auth_bypass", "idor", "business_logic", "sql_injection", "ssrf", "path_traversal"]

    def __init__(
        self,
        coverage_builder: CoverageBuilder | None = None,
        queue_manager: CandidateQueueManager | None = None,
    ) -> None:
        self.coverage_builder = coverage_builder or CoverageBuilder()
        self.queue_manager = queue_manager or CandidateQueueManager()

    def build_runtime_state(self, context: Dict[str, Any]) -> FindingRuntimeState:
        plan = self.build_audit_plan(context)
        coverage = self.coverage_builder.build(context)
        queue = self.queue_manager.build_queue(self.build_initial_candidates(context, coverage, plan))
        worker_sessions = self.build_worker_sessions(queue, plan)
        active_candidate_id = queue[0].id if queue else None
        if active_candidate_id and active_candidate_id in worker_sessions:
            worker_sessions[active_candidate_id].status = "active"
        return FindingRuntimeState(
            plan=plan,
            coverage=coverage,
            queue=queue,
            worker_sessions=worker_sessions,
            active_candidate_id=active_candidate_id,
            metrics={
                "coverage.entry_points_total": len(coverage.entry_points),
                "coverage.priority_paths_total": len(coverage.uncovered_priority_paths),
                "queue.initial_candidates": len(queue),
            },
        )

    def build_audit_plan(self, context: Dict[str, Any]) -> AuditPlan:
        config = context.get("config", {}) or {}
        focus_vulnerabilities = self._ordered_unique(
            context.get("focus_vulnerabilities")
            or config.get("focus_vulnerabilities")
            or config.get("target_vulnerabilities")
            or self.DEFAULT_VULN_FAMILIES
        )
        target_files = self._ordered_unique(context.get("target_files") or [])
        return AuditPlan(
            focus_vulnerabilities=focus_vulnerabilities,
            target_files=target_files,
            stop_conditions=[
                "closed_exploit_chain_found",
                "controller_budget_exhausted",
                "top_candidates_fully_reviewed",
            ],
        )

    def build_initial_candidates(
        self,
        context: Dict[str, Any],
        coverage: CoverageMap,
        plan: AuditPlan,
    ) -> List[CandidateCase]:
        target_files = set(plan.target_files)
        priority_paths = coverage.uncovered_priority_paths or []
        candidate_paths = self._ordered_unique(
            list(target_files)
            + priority_paths
            + [ref.split(":", 1)[0] for ref in coverage.entry_points]
            + coverage.authz_paths
        )
        entry_points = coverage.entry_points or [path for path in candidate_paths[:1] if path]

        candidates: List[CandidateCase] = []
        next_id = 1
        for vuln_family in plan.focus_vulnerabilities:
            for path in candidate_paths[: max(3, len(entry_points))]:
                related_entries = [ref for ref in entry_points if ref.startswith(path)] or entry_points[:1]
                priority = 100 if path in target_files else 80
                if any(ref.startswith(path) for ref in coverage.entry_points):
                    priority += 10
                if path in coverage.authz_paths and vuln_family in {"auth_bypass", "idor", "business_logic"}:
                    priority += 10
                candidates.append(
                    CandidateCase(
                        id=f"cand-{next_id}",
                        vuln_family=vuln_family,
                        priority=priority,
                        entry_point_refs=related_entries,
                        source_refs=related_entries.copy(),
                        sink_refs=[path],
                        control_refs=[path] if path in coverage.authz_paths else [],
                        business_flow_notes=[f"Coverage-first candidate for {vuln_family} on {path}"],
                    )
                )
                next_id += 1
        return candidates

    def build_worker_sessions(self, queue: List[CandidateCase], plan: AuditPlan) -> Dict[str, WorkerSession]:
        sessions: Dict[str, WorkerSession] = {}
        for candidate in queue:
            sessions[candidate.id] = WorkerSession(
                candidate_id=candidate.id,
                brief=self._build_candidate_brief(candidate),
                max_budget=plan.worker_budget,
                remaining_budget=plan.worker_budget,
                followup_rounds_left=plan.max_followup_rounds_per_candidate,
                message_history=[
                    {
                        "role": "user",
                        "content": self._build_candidate_brief(candidate),
                    }
                ],
            )
        return sessions

    def get_active_candidate(self, runtime_state: FindingRuntimeState) -> Optional[CandidateCase]:
        active_id = runtime_state.active_candidate_id
        if not active_id:
            return None
        for candidate in runtime_state.queue:
            if candidate.id == active_id:
                return candidate
        return None

    def rotate_candidate(self, runtime_state: FindingRuntimeState, reason: str) -> Optional[CandidateCase]:
        active_candidate = self.get_active_candidate(runtime_state)
        if active_candidate:
            session = runtime_state.worker_sessions.get(active_candidate.id)
            if session and session.status not in {"completed", "budget_exhausted"}:
                session.status = "rotated"
            if session:
                session.rotation_reason = reason
            runtime_state.rotation_history.append({"candidate_id": active_candidate.id, "reason": reason})

        next_candidate = self._next_runnable_candidate(runtime_state, after_id=active_candidate.id if active_candidate else None)
        runtime_state.active_candidate_id = next_candidate.id if next_candidate else None
        if next_candidate:
            next_session = runtime_state.worker_sessions.get(next_candidate.id)
            if next_session:
                next_session.status = "active"
        return next_candidate

    def consume_worker_budget(
        self,
        runtime_state: FindingRuntimeState,
        candidate_id: str,
        *,
        spent: int = 1,
        reason: str = "worker budget exhausted",
    ) -> Optional[CandidateCase]:
        session = runtime_state.worker_sessions.get(candidate_id)
        if not session:
            return self.get_active_candidate(runtime_state)
        session.actions_taken += spent
        session.remaining_budget = max(0, session.remaining_budget - spent)
        if session.remaining_budget == 0:
            candidate = next((item for item in runtime_state.queue if item.id == candidate_id), None)
            if candidate and candidate.evidence_bundle_ids and session.followup_rounds_left > 0:
                session.status = "needs_followup"
                session.rotation_reason = reason
                session.followup_rounds_left -= 1
                session.remaining_budget = runtime_state.plan.followup_worker_budget
                runtime_state.rotation_history.append({"candidate_id": candidate_id, "reason": reason})
            else:
                session.status = "budget_exhausted"
                session.rotation_reason = reason
                runtime_state.rotation_history.append({"candidate_id": candidate_id, "reason": reason})
            if runtime_state.active_candidate_id == candidate_id:
                next_candidate = self._next_runnable_candidate(runtime_state, after_id=candidate_id)
                runtime_state.active_candidate_id = next_candidate.id if next_candidate else None
                if next_candidate:
                    next_session = runtime_state.worker_sessions.get(next_candidate.id)
                    if next_session:
                        next_session.status = "active"
                return next_candidate
        return self.get_active_candidate(runtime_state)

    def complete_candidate(
        self,
        runtime_state: FindingRuntimeState,
        candidate_id: str,
        *,
        reason: str = "candidate completed",
    ) -> Optional[CandidateCase]:
        session = runtime_state.worker_sessions.get(candidate_id)
        if session:
            session.status = "completed"
            session.rotation_reason = reason
        runtime_state.rotation_history.append({"candidate_id": candidate_id, "reason": reason})
        if runtime_state.active_candidate_id == candidate_id:
            next_candidate = self._next_runnable_candidate(runtime_state, after_id=candidate_id)
            runtime_state.active_candidate_id = next_candidate.id if next_candidate else None
            if next_candidate:
                next_session = runtime_state.worker_sessions.get(next_candidate.id)
                if next_session:
                    next_session.status = "active"
            return next_candidate
        return self.get_active_candidate(runtime_state)

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

    def _build_candidate_brief(self, candidate: CandidateCase) -> str:
        return (
            f"Audit candidate {candidate.id} ({candidate.vuln_family}) with entry points "
            f"{candidate.entry_point_refs[:2]}, sinks {candidate.sink_refs[:2]}, and controls {candidate.control_refs[:2]}."
        )

    def _next_runnable_candidate(
        self,
        runtime_state: FindingRuntimeState,
        *,
        after_id: Optional[str] = None,
    ) -> Optional[CandidateCase]:
        candidate_ids = [candidate.id for candidate in runtime_state.queue]
        start_index = 0
        if after_id and after_id in candidate_ids:
            start_index = candidate_ids.index(after_id) + 1
        for candidate in runtime_state.queue[start_index:]:
            session = runtime_state.worker_sessions.get(candidate.id)
            if session and session.status in {"pending", "active"} and session.remaining_budget > 0:
                return candidate
        for candidate in runtime_state.queue[start_index:]:
            session = runtime_state.worker_sessions.get(candidate.id)
            if session and session.status == "needs_followup" and session.remaining_budget > 0:
                return candidate
        for candidate in runtime_state.queue[:start_index]:
            session = runtime_state.worker_sessions.get(candidate.id)
            if session and session.status in {"pending", "active"} and session.remaining_budget > 0:
                return candidate
        for candidate in runtime_state.queue[:start_index]:
            session = runtime_state.worker_sessions.get(candidate.id)
            if session and session.status == "needs_followup" and session.remaining_budget > 0:
                return candidate
        return None
