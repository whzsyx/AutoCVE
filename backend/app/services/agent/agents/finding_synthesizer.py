from __future__ import annotations

from typing import Any, Dict, List

from .finding_controller import FindingRuntimeState
from .finding_evidence import EvidenceBundleStore


class FindingSynthesizer:
    SEVERITY_MAP = {
        "auth_bypass": "high",
        "idor": "high",
        "business_logic": "high",
        "sql_injection": "critical",
        "ssrf": "high",
        "path_traversal": "high",
        "command_injection": "critical",
    }

    def synthesize(self, runtime_state: FindingRuntimeState | None, evidence_store: EvidenceBundleStore) -> Dict[str, Any]:
        if not runtime_state:
            return {"findings": [], "summary": ""}

        findings: List[Dict[str, Any]] = []
        for candidate in sorted(runtime_state.queue, key=lambda item: item.priority, reverse=True):
            if not candidate.evidence_bundle_ids:
                continue
            summary = evidence_store.build_candidate_summary(candidate.evidence_bundle_ids)
            first_bundle = evidence_store.get(candidate.evidence_bundle_ids[0])
            if not first_bundle:
                continue
            findings.append(
                {
                    "vulnerability_type": candidate.vuln_family,
                    "severity": self.SEVERITY_MAP.get(candidate.vuln_family, "high"),
                    "title": f"{candidate.vuln_family.replace('_', ' ').title()} candidate in {first_bundle.file_path}",
                    "description": first_bundle.control_analysis or first_bundle.business_flow_analysis or "Candidate reconstructed from structured audit evidence.",
                    "file_path": first_bundle.file_path,
                    "line_start": first_bundle.line_start,
                    "line_end": first_bundle.line_end,
                    "code_snippet": first_bundle.snippet,
                    "source": first_bundle.source_desc,
                    "sink": first_bundle.sink_desc,
                    "suggestion": "Validate the source-to-sink path, enforce authorization, and add sink-side guards before the dangerous operation.",
                    "confidence": max(0.7, min(first_bundle.confidence, 0.89)),
                    "needs_verification": True,
                    "verdict": "candidate",
                    "impact": first_bundle.business_flow_analysis or "Potentially exploitable according to the captured audit evidence.",
                    "cve_justification": "Structured evidence shows a candidate source-to-sink path that still requires verification closure.",
                    "verification_notes": "Synthesized from worker evidence bundles; dynamic verification is still pending.",
                    "references": [],
                    "exploit_chain": [],
                    "poc": {},
                    "entry_point_refs": summary.get("entry_point_refs", []),
                    "priority_path_refs": summary.get("priority_path_refs", []),
                    "business_flow_notes": [note for note in candidate.business_flow_notes if note],
                    "evidence_gaps": summary.get("evidence_gaps", []),
                }
            )

        summary_text = ""
        if findings:
            summary_text = (
                f"Synthesized {len(findings)} candidate findings from "
                f"{sum(1 for candidate in runtime_state.queue if candidate.evidence_bundle_ids)} evidence-backed candidates."
            )
        return {"findings": findings[:3], "summary": summary_text}
