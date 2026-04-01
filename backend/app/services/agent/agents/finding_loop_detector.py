from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Dict


@dataclass
class LoopDecision:
    status: str
    repeat_count: int
    message: str = ""


class FindingLoopDetector:
    def __init__(self, warn_threshold: int = 2, block_threshold: int = 3) -> None:
        self.warn_threshold = warn_threshold
        self.block_threshold = block_threshold
        self._counts: Dict[str, int] = {}

    def preview(self, action: str, action_input: Dict[str, Any]) -> LoopDecision:
        key = self._make_key(action, action_input)
        repeat_count = self._counts.get(key, 0)
        if repeat_count >= self.block_threshold:
            return LoopDecision(
                status="block",
                repeat_count=repeat_count,
                message="Repeated no-progress tool pattern detected. Move to another candidate or change tactics.",
            )
        return LoopDecision(status="allow", repeat_count=repeat_count)

    def register(self, action: str, action_input: Dict[str, Any], *, evidence_delta: int) -> LoopDecision:
        key = self._make_key(action, action_input)
        if evidence_delta > 0:
            self._counts[key] = 0
            return LoopDecision(status="allow", repeat_count=0)

        repeat_count = self._counts.get(key, 0) + 1
        self._counts[key] = repeat_count
        if repeat_count >= self.block_threshold:
            return LoopDecision(
                status="block",
                repeat_count=repeat_count,
                message="Repeated no-progress tool pattern detected. Stop re-reading the same path and rotate candidates.",
            )
        if repeat_count >= self.warn_threshold:
            return LoopDecision(
                status="warn",
                repeat_count=repeat_count,
                message="Repeated no-progress tool pattern detected. Try a different file, tool, or candidate.",
            )
        return LoopDecision(status="allow", repeat_count=repeat_count)

    def _make_key(self, action: str, action_input: Dict[str, Any]) -> str:
        return f"{action}:{json.dumps(action_input or {}, sort_keys=True, ensure_ascii=False)}"
