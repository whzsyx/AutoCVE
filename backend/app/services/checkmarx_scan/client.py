from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlencode, urlparse, urlunparse

import httpx


SEVERITY_LABELS = {
    0: "Info",
    1: "Low",
    2: "Medium",
    3: "High",
    4: "Critical",
}


@dataclass(frozen=True)
class CheckmarxClientConfig:
    base_url: str
    username: str
    password: str
    client_id: str
    client_secret: str | None
    scope: str
    timeout_connect: float
    timeout_read: float
    upload_timeout_read: float
    scan_timeout_seconds: int
    poll_interval_seconds: int
    preset_id: int
    force_scan: bool
    is_incremental: bool
    sast_accept_api_version: str
    help_accept_api_version: str
    help_results_delay_seconds: float
    help_results_429_max_retries: int
    help_results_429_base_wait_seconds: float


def normalize_checkmarx_base_url(url: str) -> str:
    raw = url.strip()
    if not raw:
        return raw
    parsed = urlparse(raw)
    if not parsed.scheme or not parsed.netloc:
        return raw.rstrip("/")
    return urlunparse((parsed.scheme, parsed.netloc, "", "", "", "")).rstrip("/")


def _timeout(connect: float, read: float) -> httpx.Timeout:
    return httpx.Timeout(connect=connect, read=read, write=read, pool=connect)


def _accept_headers(token: str, version: str) -> dict[str, str]:
    normalized = str(version or "default").strip()
    accept = "application/json" if normalized.lower() in ("", "default", "none") else f"application/json;v={normalized}"
    return {"Authorization": f"Bearer {token}", "Accept": accept}


def _coerce_dict(value: Any) -> dict[str, Any] | None:
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return None
        return parsed if isinstance(parsed, dict) else None
    return None


def _parse_int(raw: Any) -> int | None:
    if isinstance(raw, bool):
        return None
    if isinstance(raw, (int, float)):
        return int(raw)
    if isinstance(raw, str) and raw.strip().isdigit():
        return int(raw.strip())
    return None


def _parse_sast_results_payload(body: Any) -> list[dict[str, Any]]:
    if isinstance(body, str):
        try:
            body = json.loads(body)
        except json.JSONDecodeError:
            return []
    if isinstance(body, list):
        return [item for item in body if isinstance(item, dict)]
    if isinstance(body, dict):
        for key in ("results", "scanResults", "value", "data"):
            inner = body.get(key)
            if isinstance(inner, list):
                return [item for item in inner if isinstance(item, dict)]
        return [body] if body else []
    return []


def _parse_results_statistics(body: dict[str, Any]) -> dict[str, Any]:
    out = {
        "highSeverity": 0,
        "mediumSeverity": 0,
        "lowSeverity": 0,
        "infoSeverity": 0,
        "statisticsCalculationDate": "",
    }
    for field in ("highSeverity", "mediumSeverity", "lowSeverity", "infoSeverity"):
        value = body.get(field)
        if isinstance(value, (int, float)):
            out[field] = int(value)
    date_value = body.get("statisticsCalculationDate")
    if isinstance(date_value, str):
        out["statisticsCalculationDate"] = date_value
    out["totalSeverity"] = (
        int(out["highSeverity"])
        + int(out["mediumSeverity"])
        + int(out["lowSeverity"])
        + int(out["infoSeverity"])
    )
    return out


def _scan_finished(stats_json: dict[str, Any]) -> bool:
    value = stats_json.get("scanStatus") if stats_json.get("scanStatus") is not None else stats_json.get("scan_status")
    return isinstance(value, str) and value.strip().lower() == "finished"


def _project_id_from_json(data: Any, project_name: str) -> str | None:
    if isinstance(data, list):
        for item in data:
            if not isinstance(item, dict):
                continue
            item_name = item.get("name")
            item_id = _parse_int(item.get("id"))
            if item_id is None:
                continue
            if item_name == project_name or not project_name:
                return str(item_id)
        return None
    if isinstance(data, dict):
        item_id = _parse_int(data.get("id"))
        if item_id is not None:
            return str(item_id)
        for key in ("projects", "data", "value"):
            found = _project_id_from_json(data.get(key), project_name)
            if found:
                return found
    return None


def parse_checkmarx_result(scan_id: str, item: dict[str, Any], viewer_base_url: str) -> dict[str, Any] | None:
    data = _coerce_dict(item.get("data")) or {}
    severity = _parse_int(item.get("severity")) if item.get("severity") is not None else _parse_int(data.get("severity"))
    if severity is None or severity <= 1:
        return None

    raw_path_id = item.get("pathId", item.get("path_id"))
    if raw_path_id is None:
        raw_path_id = data.get("pathId", data.get("path_id"))
    path_id = _parse_int(raw_path_id)
    if path_id is None:
        return None

    query = item.get("query")
    if not isinstance(query, dict):
        query = data.get("query")
    query_name = None
    if isinstance(query, dict):
        query_name = query.get("name")
    if not query_name:
        query_name = item.get("queryName", item.get("query_name")) or data.get("queryName", data.get("query_name"))
    if not query_name:
        return None

    type_value = item.get("type") or data.get("type") or SEVERITY_LABELS.get(severity, str(severity))
    return {
        "scan_id": str(scan_id),
        "path_id": str(path_id),
        "vulnerability": str(query_name),
        "type": str(type_value),
        "severity": severity,
        "url": f"{viewer_base_url}/cxwebclient/ViewerMain.aspx?{urlencode({'scanid': scan_id, 'pathid': path_id})}",
        "raw": item,
    }


class CheckmarxClient:
    def __init__(self, config: CheckmarxClientConfig):
        self.config = config
        self.api_root = normalize_checkmarx_base_url(config.base_url)

    async def authenticate(self) -> str:
        token_url = f"{self.api_root}/cxrestapi/auth/identity/connect/token"
        timeout = _timeout(self.config.timeout_connect, self.config.timeout_read)
        payload = {
            "username": self.config.username,
            "password": self.config.password,
            "grant_type": "password",
            "scope": self.config.scope,
            "client_id": self.config.client_id,
            "client_secret": self.config.client_secret or "",
        }
        fallback = {
            "username": self.config.username,
            "password": self.config.password,
            "grant_type": "password",
            "scope": "sast_rest_api",
            "client_id": "resource_owner_client",
        }
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.post(token_url, data=payload, headers={"Content-Type": "application/x-www-form-urlencoded"})
            if response.status_code != 200:
                response = await client.post(token_url, data=fallback, headers={"Content-Type": "application/x-www-form-urlencoded"})
        response.raise_for_status()
        body = response.json()
        token = body.get("access_token")
        if not isinstance(token, str) or len(token) < 10:
            raise RuntimeError("Checkmarx authentication response did not contain a valid access_token")
        return token

    async def get_project_id(self, token: str, project_name: str) -> str:
        url = f"{self.api_root}/cxrestapi/projects"
        timeout = _timeout(self.config.timeout_connect, self.config.timeout_read)
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.get(
                url,
                headers={"Authorization": f"Bearer {token}"},
                params={"projectName": project_name},
            )
        response.raise_for_status()
        project_id = _project_id_from_json(response.json(), project_name)
        if not project_id:
            raise RuntimeError(f"Checkmarx project does not exist: {project_name}")
        return project_id

    async def submit_scan(self, token: str, project_id: str, source_zip_path: str | Path) -> str:
        zip_path = Path(source_zip_path)
        if not zip_path.is_file():
            raise FileNotFoundError(f"Source ZIP does not exist: {zip_path}")
        url = f"{self.api_root}/cxrestapi/sast/scanWithSettings"
        timeout = _timeout(self.config.timeout_connect, self.config.upload_timeout_read)
        data = {
            "projectId": str(project_id),
            "overrideProjectSetting": "false",
            "forceScan": "true" if self.config.force_scan else "false",
            "isIncremental": "true" if self.config.is_incremental else "false",
            "presetId": str(int(self.config.preset_id)),
        }
        async with httpx.AsyncClient(timeout=timeout) as client:
            with zip_path.open("rb") as source:
                response = await client.post(
                    url,
                    headers={"Authorization": f"Bearer {token}"},
                    data=data,
                    files={"zippedSource": (zip_path.name, source, "application/zip")},
                )
        response.raise_for_status()
        body = response.json()
        scan_id = _parse_int(body.get("id"))
        if scan_id is None:
            raise RuntimeError("Checkmarx scan submission response did not contain a valid scan id")
        return str(scan_id)

    async def wait_for_results(self, token: str, scan_id: str) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        import asyncio

        stats_url = f"{self.api_root}/cxrestapi/sast/scans/{scan_id}/statistics"
        result_stats_url = f"{self.api_root}/cxrestapi/sast/scans/{scan_id}/resultsStatistics"
        timeout = _timeout(self.config.timeout_connect, self.config.timeout_read)
        headers = _accept_headers(token, self.config.sast_accept_api_version)

        async with httpx.AsyncClient(timeout=timeout) as client:
            elapsed = 0
            while elapsed < self.config.scan_timeout_seconds:
                response = await client.get(stats_url, headers=headers)
                if response.status_code not in (200, 404):
                    response.raise_for_status()
                stats_body = response.json() if response.status_code == 200 else {}
                if isinstance(stats_body, dict) and _scan_finished(stats_body):
                    result_stats = await client.get(result_stats_url, headers=headers)
                    result_stats.raise_for_status()
                    totals = _parse_results_statistics(result_stats.json())
                    rows = await self._fetch_filtered_results(client, token, scan_id, int(totals["totalSeverity"]))
                    return totals, rows
                await asyncio.sleep(self.config.poll_interval_seconds)
                elapsed += self.config.poll_interval_seconds

        raise TimeoutError(f"Checkmarx scan timed out after {self.config.scan_timeout_seconds}s, scan_id={scan_id}")

    async def _fetch_filtered_results(
        self,
        client: httpx.AsyncClient,
        token: str,
        scan_id: str,
        total_count: int,
    ) -> list[dict[str, Any]]:
        import asyncio

        rows: list[dict[str, Any]] = []
        if total_count <= 0:
            return rows

        url = f"{self.api_root}/cxrestapi/help/sast/results"
        headers = _accept_headers(token, self.config.help_accept_api_version)
        for offset in range(total_count):
            if self.config.help_results_delay_seconds > 0 and offset > 0:
                await asyncio.sleep(self.config.help_results_delay_seconds)

            response: httpx.Response | None = None
            for attempt in range(self.config.help_results_429_max_retries + 1):
                response = await client.get(url, headers=headers, params={"scanId": scan_id, "offset": offset, "limit": 1})
                if response.status_code != 429:
                    break
                wait_seconds = self.config.help_results_429_base_wait_seconds * (2**attempt)
                await asyncio.sleep(max(1.0, wait_seconds))
            if response is None:
                break
            if response.status_code != 200:
                response.raise_for_status()
            items = _parse_sast_results_payload(response.text)
            if not items:
                break
            for item in items:
                parsed = parse_checkmarx_result(scan_id, item, self.api_root)
                if parsed:
                    rows.append(parsed)
        return rows
