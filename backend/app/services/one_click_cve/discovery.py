from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable, Iterable

import httpx

from app.core.config import settings


SECURITY_POLICY_PATHS = (
    "SECURITY.md",
    ".github/SECURITY.md",
    "docs/SECURITY.md",
)

GITHUB_SOFT_FAILURE_STATUS_CODES = {403, 404, 429, 451}

WEB_APP_TERMS = (
    "",
    "self-hosted",
    "cms",
    "crm",
    "dashboard",
    "admin",
    "chat",
    "workflow",
    "analytics",
)

WEB_APP_KEYWORDS = (
    "self-hosted",
    "cms",
    "crm",
    "dashboard",
    "admin",
    "panel",
    "chat",
    "workflow",
    "server",
    "api",
    "web",
    "application",
)

VERY_POPULAR_STARS_THRESHOLD = 100_000
PREFERRED_STARS_THRESHOLD = 30_000
SECONDARY_STARS_THRESHOLD = 50_000
MAX_ADVISORY_COUNT_SCORE = 100
ADVISORY_COUNT_SCORE = 45.0
MIN_ADVISORY_ENRICHMENT_CANDIDATES = 10
MAX_ENRICHMENT_CANDIDATES = 20
MAX_RAW_SEARCH_RESULTS = 60


@dataclass(frozen=True)
class GitHubRepositoryCandidate:
    full_name: str
    repository_url: str
    description: str | None
    language: str | None
    stars: int
    pushed_at: datetime | None
    updated_at: datetime | None
    default_branch: str
    version_label: str
    version_source: str
    has_security_advisory: bool
    advisory_count: int
    has_security_policy: bool
    score: float
    has_private_vulnerability_reporting: bool = False


class GitHubApiClient:
    def __init__(self, token: str | None = None, timeout_seconds: float = 30.0):
        self.token = token or settings.GITHUB_TOKEN
        self.timeout_seconds = timeout_seconds

    def _headers(self) -> dict[str, str]:
        headers = {
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        return headers

    async def get_json(self, path: str, params: dict | None = None) -> Any:
        async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
            response = await client.get(
                f"https://api.github.com{path}",
                params=params,
                headers=self._headers(),
            )
            if response.status_code == 404:
                return None
            response.raise_for_status()
            return response.json()

    async def exists(self, path: str) -> bool:
        async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
            response = await client.get(f"https://api.github.com{path}", headers=self._headers())
            if response.status_code == 200:
                return True
            if response.status_code == 404:
                return False
            response.raise_for_status()
            return False


class GitHubCveDiscoveryService:
    def __init__(
        self,
        *,
        client: GitHubApiClient | Any | None = None,
        now_provider: Callable[[], datetime] | None = None,
    ):
        self.client = client or GitHubApiClient()
        self.now_provider = now_provider or (lambda: datetime.now(timezone.utc))

    async def discover_candidates(
        self,
        *,
        target_count: int,
        excluded_full_names: Iterable[str] | None = None,
        prefer_security_advisory: bool = True,
    ) -> list[GitHubRepositoryCandidate]:
        cutoff = _subtract_months(self.now_provider(), 6)
        excluded = {item.lower() for item in (excluded_full_names or [])}
        raw_items: dict[str, dict[str, Any]] = {}
        requested = max(1, int(target_count))
        search_limit = max(requested, min(MAX_RAW_SEARCH_RESULTS, max(20, requested * 4)))

        for term in WEB_APP_TERMS:
            if len(raw_items) >= search_limit:
                break
            query = _build_search_query(term=term, cutoff=cutoff, prefer_security_advisory=prefer_security_advisory)
            try:
                payload = await self.client.get_json(
                    "/search/repositories",
                    params={
                        "q": query,
                        "sort": "updated",
                        "order": "desc",
                        "per_page": min(50, search_limit),
                        "page": 1,
                    },
                )
            except (httpx.HTTPStatusError, httpx.RequestError) as exc:
                if _is_transient_github_failure(exc):
                    break
                raise
            for item in (payload or {}).get("items", []):
                full_name = str(item.get("full_name") or "").strip()
                if full_name and full_name.lower() not in excluded:
                    raw_items.setdefault(full_name, item)
                if len(raw_items) >= search_limit:
                    break

        enriched: list[GitHubRepositoryCandidate] = []
        ranked_items = [
            item for item in raw_items.values() if _is_basic_candidate_item(item, cutoff, prefer_security_advisory=prefer_security_advisory)
        ]
        ranked_items.sort(
            key=lambda item: _raw_candidate_sort_key(item, now=self.now_provider(), prefer_security_advisory=prefer_security_advisory),
            reverse=True,
        )
        enrichment_limit = requested
        if prefer_security_advisory:
            enrichment_limit = min(
                len(ranked_items),
                max(requested, min(MAX_ENRICHMENT_CANDIDATES, max(MIN_ADVISORY_ENRICHMENT_CANDIDATES, requested * 3))),
            )

        for item in ranked_items[:enrichment_limit]:
            candidate = await self._candidate_from_item(item, cutoff, prefer_security_advisory=prefer_security_advisory)
            if candidate is not None:
                enriched.append(candidate)

        enriched.sort(key=lambda candidate: (candidate.score, candidate.pushed_at or datetime.min.replace(tzinfo=timezone.utc)), reverse=True)
        return enriched[:requested]

    async def _candidate_from_item(
        self,
        item: dict[str, Any],
        cutoff: datetime,
        *,
        prefer_security_advisory: bool,
    ) -> GitHubRepositoryCandidate | None:
        full_name = str(item.get("full_name") or "").strip()
        if not full_name or "/" not in full_name:
            return None
        if bool(item.get("archived")) or bool(item.get("fork")):
            return None

        stars = int(item.get("stargazers_count") or 0)
        pushed_at = _parse_github_datetime(item.get("pushed_at"))
        updated_at = _parse_github_datetime(item.get("updated_at"))
        if stars <= 1000 or (pushed_at and pushed_at < cutoff):
            return None

        owner, repo = full_name.split("/", 1)
        advisories = await self._safe_get_advisories(owner, repo) if prefer_security_advisory else []
        advisory_count = len(advisories)
        has_private_vulnerability_reporting = await self._has_private_vulnerability_reporting(owner, repo) if prefer_security_advisory else False
        has_security_policy = await self._has_security_policy(owner, repo) if prefer_security_advisory else False
        default_branch = str(item.get("default_branch") or "main")
        version_label, version_source = await self._resolve_project_version(owner, repo, default_branch)
        description = item.get("description")
        language = item.get("language")
        score = _score_candidate(
            stars=stars,
            pushed_at=pushed_at,
            now=self.now_provider(),
            has_security_advisory=advisory_count > 0,
            advisory_count=advisory_count,
            has_security_policy=has_security_policy,
            has_private_vulnerability_reporting=has_private_vulnerability_reporting,
            description=str(description or ""),
            language=str(language or ""),
            full_name=full_name,
        )

        return GitHubRepositoryCandidate(
            full_name=full_name,
            repository_url=str(item.get("html_url") or f"https://github.com/{full_name}"),
            description=str(description) if description else None,
            language=str(language) if language else None,
            stars=stars,
            pushed_at=pushed_at,
            updated_at=updated_at,
            default_branch=default_branch,
            version_label=version_label,
            version_source=version_source,
            has_security_advisory=advisory_count > 0,
            advisory_count=advisory_count,
            has_security_policy=has_security_policy,
            score=score,
            has_private_vulnerability_reporting=has_private_vulnerability_reporting,
        )

    async def _safe_get_advisories(self, owner: str, repo: str) -> list[dict[str, Any]]:
        try:
            payload = await self.client.get_json(
                f"/repos/{owner}/{repo}/security-advisories",
                params={"state": "published", "per_page": MAX_ADVISORY_COUNT_SCORE},
            )
        except (httpx.HTTPStatusError, httpx.RequestError) as exc:
            if _is_transient_github_failure(exc):
                return []
            raise
        if isinstance(payload, list):
            return [item for item in payload if isinstance(item, dict)]
        return []

    async def _has_security_policy(self, owner: str, repo: str) -> bool:
        for path in SECURITY_POLICY_PATHS:
            try:
                if await self.client.exists(f"/repos/{owner}/{repo}/contents/{path}"):
                    return True
            except (httpx.HTTPStatusError, httpx.RequestError) as exc:
                if isinstance(exc, httpx.RequestError):
                    return False
                if _is_soft_github_failure(exc):
                    continue
                raise
        return False

    async def _has_private_vulnerability_reporting(self, owner: str, repo: str) -> bool:
        try:
            payload = await self.client.get_json(f"/repos/{owner}/{repo}/private-vulnerability-reporting")
        except (httpx.HTTPStatusError, httpx.RequestError) as exc:
            if isinstance(exc, httpx.HTTPStatusError) and exc.response.status_code == 422:
                return False
            if _is_transient_github_failure(exc):
                return False
            raise
        return bool(payload.get("enabled")) if isinstance(payload, dict) else False

    async def _resolve_project_version(self, owner: str, repo: str, default_branch: str) -> tuple[str, str]:
        latest_release = await self._safe_get_latest_release(owner, repo)
        if latest_release:
            tag_name = str(latest_release.get("tag_name") or "").strip()
            if tag_name:
                return tag_name, "latest_release"

        latest_tag = await self._safe_get_latest_tag(owner, repo)
        if latest_tag:
            return latest_tag, "latest_tag"
        return default_branch or "main", "default_branch"

    async def _safe_get_latest_release(self, owner: str, repo: str) -> dict[str, Any] | None:
        try:
            payload = await self.client.get_json(f"/repos/{owner}/{repo}/releases/latest")
        except (httpx.HTTPStatusError, httpx.RequestError) as exc:
            if _is_transient_github_failure(exc):
                return None
            raise
        return payload if isinstance(payload, dict) else None

    async def _safe_get_latest_tag(self, owner: str, repo: str) -> str | None:
        try:
            payload = await self.client.get_json(f"/repos/{owner}/{repo}/tags", params={"per_page": 1})
        except (httpx.HTTPStatusError, httpx.RequestError) as exc:
            if _is_transient_github_failure(exc):
                return None
            raise
        if isinstance(payload, list) and payload:
            tag_name = str((payload[0] or {}).get("name") or "").strip()
            return tag_name or None
        return None


def _build_search_query(*, term: str, cutoff: datetime, prefer_security_advisory: bool = True) -> str:
    pieces = ["stars:>1000", f"pushed:>={cutoff.date().isoformat()}", "archived:false", "fork:false"]
    if term:
        pieces.append(term)
    return " ".join(pieces)


def _is_soft_github_failure(exc: httpx.HTTPStatusError) -> bool:
    return exc.response.status_code in GITHUB_SOFT_FAILURE_STATUS_CODES


def _is_transient_github_failure(exc: httpx.HTTPError) -> bool:
    if isinstance(exc, httpx.HTTPStatusError):
        return _is_soft_github_failure(exc)
    return isinstance(exc, httpx.RequestError)


def _is_basic_candidate_item(item: dict[str, Any], cutoff: datetime, *, prefer_security_advisory: bool = True) -> bool:
    full_name = str(item.get("full_name") or "").strip()
    if not full_name or "/" not in full_name:
        return False
    if bool(item.get("archived")) or bool(item.get("fork")):
        return False
    stars = int(item.get("stargazers_count") or 0)
    pushed_at = _parse_github_datetime(item.get("pushed_at"))
    if stars <= 1000:
        return False
    return not (pushed_at and pushed_at < cutoff)


def _raw_candidate_sort_key(item: dict[str, Any], *, now: datetime, prefer_security_advisory: bool = True) -> tuple[float, datetime]:
    pushed_at = _parse_github_datetime(item.get("pushed_at"))
    score = _score_candidate(
        stars=int(item.get("stargazers_count") or 0),
        pushed_at=pushed_at,
        now=now,
        has_security_advisory=False,
        advisory_count=0,
        has_security_policy=False,
        has_private_vulnerability_reporting=False,
        description=str(item.get("description") or ""),
        language=str(item.get("language") or ""),
        full_name=str(item.get("full_name") or ""),
    )
    return score, pushed_at or datetime.min.replace(tzinfo=timezone.utc)


def _parse_github_datetime(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        text = str(value).replace("Z", "+00:00")
        parsed = datetime.fromisoformat(text)
        if parsed.tzinfo is None:
            return parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)
    except ValueError:
        return None


def _subtract_months(value: datetime, months: int) -> datetime:
    value = value.astimezone(timezone.utc) if value.tzinfo else value.replace(tzinfo=timezone.utc)
    month_index = value.month - months
    year = value.year
    while month_index <= 0:
        month_index += 12
        year -= 1
    day = min(value.day, _days_in_month(year, month_index))
    return value.replace(year=year, month=month_index, day=day)


def _days_in_month(year: int, month: int) -> int:
    if month == 2:
        if (year % 4 == 0 and year % 100 != 0) or year % 400 == 0:
            return 29
        return 28
    if month in {4, 6, 9, 11}:
        return 30
    return 31


def _score_candidate(
    *,
    stars: int,
    pushed_at: datetime | None,
    now: datetime,
    has_security_advisory: bool,
    advisory_count: int,
    has_security_policy: bool,
    has_private_vulnerability_reporting: bool,
    description: str,
    language: str,
    full_name: str,
) -> float:
    score = _star_priority_score(stars)
    if has_security_advisory:
        score += 900 + min(advisory_count, MAX_ADVISORY_COUNT_SCORE) * ADVISORY_COUNT_SCORE
    if has_private_vulnerability_reporting:
        score += 300
    if has_security_advisory and has_private_vulnerability_reporting:
        score += 1500
    if has_security_policy:
        score += 80
    if pushed_at:
        age_days = max(0, (now.astimezone(timezone.utc) - pushed_at).days)
        score += max(0.0, 60.0 - min(age_days, 180) / 3)

    haystack = f"{full_name} {description}".lower()
    score += sum(8 for keyword in WEB_APP_KEYWORDS if keyword in haystack)
    if language.lower() in {"php", "javascript", "typescript", "python", "go", "ruby", "java", "rust"}:
        score += 12
    return score


def _star_priority_score(stars: int) -> float:
    if stars <= 1000:
        return 0.0
    if stars <= PREFERRED_STARS_THRESHOLD:
        return 520.0 + min(stars, PREFERRED_STARS_THRESHOLD) / PREFERRED_STARS_THRESHOLD * 35.0
    if stars <= SECONDARY_STARS_THRESHOLD:
        distance = (stars - PREFERRED_STARS_THRESHOLD) / (SECONDARY_STARS_THRESHOLD - PREFERRED_STARS_THRESHOLD)
        return 485.0 - distance * 25.0
    if stars <= VERY_POPULAR_STARS_THRESHOLD:
        distance = (stars - SECONDARY_STARS_THRESHOLD) / (VERY_POPULAR_STARS_THRESHOLD - SECONDARY_STARS_THRESHOLD)
        return 405.0 - distance * 75.0
    return max(120.0, 260.0 - min((stars - VERY_POPULAR_STARS_THRESHOLD) / 1000.0, 140.0))
