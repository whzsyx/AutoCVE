from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

import httpx


PROMPT_TYPE = "sast-result-extra"
MARK_REAL_VULN = "**真实漏洞**"
MARK_EXPLAIN = "**解释**"


@dataclass(frozen=True)
class WorkflowVerdict:
    real_vuln: bool | None
    reason: str | None
    raw_text: str


def _normalize_bearer_token(token: str) -> str:
    value = (token or "").strip()
    if value.lower().startswith("bearer "):
        return value[7:].strip()
    return value


def build_workflow_payload(scan_id: str, path_id: str, vul_type: str, user: str) -> dict[str, Any]:
    return {
        "inputs": {
            "path_id": str(path_id),
            "scan_id": str(scan_id),
            "prompt_type": PROMPT_TYPE,
            "vul_type": str(vul_type),
        },
        "response_mode": "blocking",
        "user": user,
    }


def workflow_answer_text(body: Any) -> str:
    if isinstance(body, str):
        return body
    if not isinstance(body, dict):
        return ""

    data = body.get("data")
    if isinstance(data, dict):
        outputs = data.get("outputs")
        if isinstance(outputs, dict):
            chunks: list[str] = []
            for value in outputs.values():
                if isinstance(value, str):
                    chunks.append(value)
                elif isinstance(value, (dict, list)):
                    chunks.append(json.dumps(value, ensure_ascii=False))
            if chunks:
                return "\n".join(chunks)
        for key in ("output", "text", "answer"):
            value = data.get(key)
            if isinstance(value, str) and value.strip():
                return value

    for key in ("answer", "result", "text"):
        value = body.get(key)
        if isinstance(value, str) and value.strip():
            return value
    return json.dumps(body, ensure_ascii=False)


def split_real_vuln_and_explain(text: str) -> list[dict[str, str | None]]:
    pairs: list[dict[str, str | None]] = []
    if not text:
        return pairs

    pos = 0
    real_mark_len = len(MARK_REAL_VULN)
    explain_mark_len = len(MARK_EXPLAIN)

    while True:
        real_idx = text.find(MARK_REAL_VULN, pos)
        if real_idx == -1:
            break

        rest = text[real_idx + real_mark_len :]
        next_real_idx = rest.find(MARK_REAL_VULN)
        explain_idx = rest.find(MARK_EXPLAIN)
        if explain_idx == -1:
            break
        if next_real_idx != -1 and next_real_idx < explain_idx:
            pos = real_idx + real_mark_len + next_real_idx
            continue

        real_part = rest[:explain_idx].strip() or None
        explain_rest = rest[explain_idx + explain_mark_len :]
        next_real_in_explain = explain_rest.find(MARK_REAL_VULN)
        if next_real_in_explain == -1:
            explain_part = explain_rest.strip() or None
            pairs.append({"parsed_real_vuln": real_part, "parsed_explanation": explain_part})
            break

        explain_part = explain_rest[:next_real_in_explain].strip() or None
        pairs.append({"parsed_real_vuln": real_part, "parsed_explanation": explain_part})
        pos = real_idx + real_mark_len + explain_idx + explain_mark_len + next_real_in_explain

    return pairs[1:] if len(pairs) > 1 else pairs


def infer_real_vuln(text: str | None) -> bool | None:
    if not text:
        return None
    has_true = "是" in text or "true" in text.lower()
    has_false = "否" in text or "false" in text.lower()
    if has_true and has_false:
        return None
    if has_true:
        return True
    if has_false:
        return False
    return None


def parse_workflow_verdict(answer_text: str) -> WorkflowVerdict:
    segments = split_real_vuln_and_explain(answer_text)
    last_segment = segments[-1] if segments else {}
    real_text = last_segment.get("parsed_real_vuln")
    reason = last_segment.get("parsed_explanation")
    return WorkflowVerdict(
        real_vuln=infer_real_vuln(real_text),
        reason=reason,
        raw_text=answer_text,
    )


async def request_workflow_verdict(
    *,
    url: str,
    token: str,
    payload: dict[str, Any],
    timeout_seconds: float,
) -> tuple[WorkflowVerdict, Any]:
    normalized_token = _normalize_bearer_token(token)
    async with httpx.AsyncClient(timeout=timeout_seconds) as client:
        response = await client.post(
            url,
            json=payload,
            headers={
                "Content-Type": "application/json; charset=utf-8",
                "Authorization": f"Bearer {normalized_token}",
            },
        )
    response.raise_for_status()
    try:
        body: Any = response.json()
    except json.JSONDecodeError:
        body = response.text
    answer_text = workflow_answer_text(body)
    return parse_workflow_verdict(answer_text), body

