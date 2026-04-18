from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

GUARDRAILS_METADATA_KEY = "guardrails"
WRITE_APPROVALS_METADATA_KEY = "write_approvals"
SHELL_APPROVALS_METADATA_KEY = "shell_approvals"


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def normalize_relative_path(path: str) -> str:
    return Path(str(path or "").strip()).as_posix().lstrip("./")


def normalize_command(command: str) -> str:
    return " ".join(str(command or "").strip().split())


def is_guardrails_enabled(runtime_state: Any) -> bool:
    metadata = dict(getattr(runtime_state, "metadata", {}) or {})
    config = dict(metadata.get(GUARDRAILS_METADATA_KEY) or {})
    return bool(config.get("enabled") is True)


def set_guardrails_enabled(runtime_state: Any, enabled: bool) -> bool:
    metadata = runtime_state.metadata.setdefault(GUARDRAILS_METADATA_KEY, {})
    metadata["enabled"] = bool(enabled)
    metadata["updated_at"] = _utc_now()
    return bool(metadata["enabled"])


def register_write_approval(
    runtime_state: Any,
    *,
    path: str,
    guardrail_code: str,
    tool_call_id: str | None = None,
) -> dict[str, Any]:
    normalized_path = normalize_relative_path(path)
    approvals = runtime_state.metadata.setdefault(WRITE_APPROVALS_METADATA_KEY, [])
    for item in approvals:
        if (
            normalize_relative_path(str(item.get("path") or "")) == normalized_path
            and str(item.get("guardrail_code") or "") == str(guardrail_code or "")
        ):
            item["approved_at"] = _utc_now()
            if tool_call_id:
                item["tool_call_id"] = tool_call_id
            return dict(item)
    record = {
        "path": normalized_path,
        "guardrail_code": str(guardrail_code or "").strip(),
        "tool_call_id": str(tool_call_id or "").strip() or None,
        "approved_at": _utc_now(),
    }
    approvals.append(record)
    return dict(record)


def has_write_approval(
    runtime_state: Any,
    *,
    project_root: Path,
    resolved_path: Path,
    guardrail_code: str,
) -> bool:
    approvals = runtime_state.metadata.get(WRITE_APPROVALS_METADATA_KEY) or []
    try:
        relative_path = resolved_path.relative_to(project_root).as_posix()
    except ValueError:
        relative_path = str(resolved_path)
    normalized_relative_path = normalize_relative_path(relative_path)
    for item in approvals:
        if normalize_relative_path(str(item.get("path") or "")) != normalized_relative_path:
            continue
        if str(item.get("guardrail_code") or "") != str(guardrail_code or ""):
            continue
        return True
    return False


def register_shell_approval(
    runtime_state: Any,
    *,
    tool_name: str,
    command: str,
    guardrail_code: str,
    tool_call_id: str | None = None,
) -> dict[str, Any]:
    normalized_tool_name = str(tool_name or "").strip()
    normalized_command = normalize_command(command)
    approvals = runtime_state.metadata.setdefault(SHELL_APPROVALS_METADATA_KEY, [])
    for item in approvals:
        if str(item.get("tool_name") or "") != normalized_tool_name:
            continue
        if normalize_command(str(item.get("command") or "")) != normalized_command:
            continue
        if str(item.get("guardrail_code") or "") != str(guardrail_code or ""):
            continue
        item["approved_at"] = _utc_now()
        if tool_call_id:
            item["tool_call_id"] = tool_call_id
        return dict(item)
    record = {
        "tool_name": normalized_tool_name,
        "command": normalized_command,
        "guardrail_code": str(guardrail_code or "").strip(),
        "tool_call_id": str(tool_call_id or "").strip() or None,
        "approved_at": _utc_now(),
    }
    approvals.append(record)
    return dict(record)


def has_shell_approval(
    runtime_state: Any,
    *,
    tool_name: str,
    command: str,
    guardrail_code: str,
) -> bool:
    normalized_tool_name = str(tool_name or "").strip()
    normalized_command = normalize_command(command)
    approvals = runtime_state.metadata.get(SHELL_APPROVALS_METADATA_KEY) or []
    for item in approvals:
        if str(item.get("tool_name") or "") != normalized_tool_name:
            continue
        if normalize_command(str(item.get("command") or "")) != normalized_command:
            continue
        if str(item.get("guardrail_code") or "") != str(guardrail_code or ""):
            continue
        return True
    return False
