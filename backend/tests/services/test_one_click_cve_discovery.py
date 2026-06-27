from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

import httpx
import pytest

from app.services.one_click_cve.discovery import GitHubCveDiscoveryService


class FakeGitHubClient:
    def __init__(self):
        self.calls: list[str] = []

    async def get_json(self, path: str, params: dict | None = None):
        self.calls.append(path)
        if path == "/search/repositories":
            return {
                "items": [
                    {
                        "full_name": "plain/webapp",
                        "html_url": "https://github.com/plain/webapp",
                        "description": "Self-hosted dashboard application",
                        "stargazers_count": 4100,
                        "pushed_at": "2026-05-18T12:00:00Z",
                        "updated_at": "2026-05-18T12:00:00Z",
                        "archived": False,
                        "fork": False,
                        "default_branch": "main",
                        "language": "TypeScript",
                    },
                    {
                        "full_name": "secure/cms",
                        "html_url": "https://github.com/secure/cms",
                        "description": "Open source CMS with admin panel",
                        "stargazers_count": 1700,
                        "pushed_at": "2026-05-02T12:00:00Z",
                        "updated_at": "2026-05-02T12:00:00Z",
                        "archived": False,
                        "fork": False,
                        "default_branch": "develop",
                        "language": "PHP",
                    },
                    {
                        "full_name": "old/project",
                        "html_url": "https://github.com/old/project",
                        "description": "Old project",
                        "stargazers_count": 9000,
                        "pushed_at": "2025-08-01T12:00:00Z",
                        "updated_at": "2025-08-01T12:00:00Z",
                        "archived": False,
                        "fork": False,
                        "default_branch": "main",
                        "language": "Python",
                    },
                    {
                        "full_name": "fork/project",
                        "html_url": "https://github.com/fork/project",
                        "description": "Forked project",
                        "stargazers_count": 5000,
                        "pushed_at": "2026-05-01T12:00:00Z",
                        "updated_at": "2026-05-01T12:00:00Z",
                        "archived": False,
                        "fork": True,
                        "default_branch": "main",
                        "language": "Go",
                    },
                ]
            }
        if path == "/repos/secure/cms/security-advisories":
            return [{"ghsa_id": "GHSA-aaaa-bbbb-cccc"}]
        if path == "/repos/secure/cms/private-vulnerability-reporting":
            return {"enabled": True}
        if path.endswith("/private-vulnerability-reporting"):
            return {"enabled": False}
        if path.endswith("/security-advisories"):
            return []
        if path == "/repos/secure/cms/releases/latest":
            return {"tag_name": "v3.85.0"}
        if path.endswith("/releases/latest"):
            return None
        if path.endswith("/tags"):
            return []
        raise AssertionError(f"unexpected path: {path}")

    async def exists(self, path: str) -> bool:
        self.calls.append(path)
        return path == "/repos/plain/webapp/contents/SECURITY.md"


@pytest.mark.asyncio
async def test_discovers_recent_starred_repositories_and_prioritizes_advisories():
    service = GitHubCveDiscoveryService(
        client=FakeGitHubClient(),
        now_provider=lambda: datetime(2026, 6, 6, tzinfo=timezone.utc),
    )

    candidates = await service.discover_candidates(target_count=2, excluded_full_names={"already/scanned"})

    assert [candidate.full_name for candidate in candidates] == ["secure/cms", "plain/webapp"]
    assert candidates[0].has_security_advisory is True
    assert candidates[0].advisory_count == 1
    assert candidates[0].default_branch == "develop"
    assert candidates[0].version_label == "v3.85.0"
    assert candidates[0].version_source == "latest_release"
    assert candidates[1].has_security_policy is True


class RepositorySizeLimitClient:
    def __init__(self):
        self.search_queries: list[str] = []

    async def get_json(self, path: str, params: dict | None = None):
        if path == "/search/repositories":
            self.search_queries.append(str((params or {}).get("q") or ""))
            return {
                "items": [
                    {
                        "full_name": "too-big/platform",
                        "html_url": "https://github.com/too-big/platform",
                        "description": "Self-hosted platform",
                        "stargazers_count": 9000,
                        "pushed_at": "2026-05-18T12:00:00Z",
                        "updated_at": "2026-05-18T12:00:00Z",
                        "archived": False,
                        "fork": False,
                        "default_branch": "main",
                        "language": "TypeScript",
                        "size": 512001,
                    },
                    {
                        "full_name": "right-sized/platform",
                        "html_url": "https://github.com/right-sized/platform",
                        "description": "Self-hosted platform",
                        "stargazers_count": 8000,
                        "pushed_at": "2026-05-18T12:00:00Z",
                        "updated_at": "2026-05-18T12:00:00Z",
                        "archived": False,
                        "fork": False,
                        "default_branch": "main",
                        "language": "TypeScript",
                        "size": 512000,
                    },
                ]
            }
        if path.endswith("/security-advisories"):
            return []
        if path.endswith("/private-vulnerability-reporting"):
            return {"enabled": False}
        if path.endswith("/releases/latest"):
            return None
        if path.endswith("/tags"):
            return []
        raise AssertionError(f"unexpected path: {path}")

    async def exists(self, path: str) -> bool:
        return False


@pytest.mark.asyncio
async def test_discovery_limits_repositories_to_configured_size(monkeypatch):
    client = RepositorySizeLimitClient()
    monkeypatch.setattr("app.services.one_click_cve.discovery.settings.ONE_CLICK_CVE_MAX_REPOSITORY_SIZE_KB", 512000)
    service = GitHubCveDiscoveryService(
        client=client,
        now_provider=lambda: datetime(2026, 6, 6, tzinfo=timezone.utc),
    )

    candidates = await service.discover_candidates(target_count=2)

    assert all("size:<=512000" in query for query in client.search_queries)
    assert [candidate.full_name for candidate in candidates] == ["right-sized/platform"]


class RateLimitedPolicyClient:
    async def get_json(self, path: str, params: dict | None = None):
        if path == "/search/repositories":
            return {
                "items": [
                    {
                        "full_name": "limited/webapp",
                        "html_url": "https://github.com/limited/webapp",
                        "description": "Self-hosted admin dashboard",
                        "stargazers_count": 2400,
                        "pushed_at": "2026-05-18T12:00:00Z",
                        "updated_at": "2026-05-18T12:00:00Z",
                        "archived": False,
                        "fork": False,
                        "default_branch": "main",
                        "language": "TypeScript",
                    },
                ]
            }
        if path.endswith("/security-advisories"):
            return []
        if path.endswith("/private-vulnerability-reporting"):
            return {"enabled": False}
        if path.endswith("/releases/latest"):
            return None
        if path.endswith("/tags"):
            return []
        raise AssertionError(f"unexpected path: {path}")

    async def exists(self, path: str) -> bool:
        request = httpx.Request("GET", f"https://api.github.com{path}")
        response = httpx.Response(429, request=request)
        raise httpx.HTTPStatusError("too many requests", request=request, response=response)


@pytest.mark.asyncio
async def test_discovers_candidates_when_security_policy_probe_is_rate_limited():
    service = GitHubCveDiscoveryService(
        client=RateLimitedPolicyClient(),
        now_provider=lambda: datetime(2026, 6, 6, tzinfo=timezone.utc),
    )

    candidates = await service.discover_candidates(target_count=1)

    assert [candidate.full_name for candidate in candidates] == ["limited/webapp"]
    assert candidates[0].has_security_policy is False


class SearchRateLimitedAfterFirstPageClient:
    def __init__(self):
        self.search_calls = 0

    async def get_json(self, path: str, params: dict | None = None):
        if path == "/search/repositories":
            self.search_calls += 1
            if self.search_calls > 1:
                request = httpx.Request("GET", "https://api.github.com/search/repositories")
                response = httpx.Response(429, request=request)
                raise httpx.HTTPStatusError("too many requests", request=request, response=response)
            return {
                "items": [
                    {
                        "full_name": "first/result",
                        "html_url": "https://github.com/first/result",
                        "description": "Self-hosted workflow server",
                        "stargazers_count": 3200,
                        "pushed_at": "2026-05-20T12:00:00Z",
                        "updated_at": "2026-05-20T12:00:00Z",
                        "archived": False,
                        "fork": False,
                        "default_branch": "main",
                        "language": "Python",
                    },
                ]
            }
        if path.endswith("/security-advisories"):
            return []
        if path.endswith("/private-vulnerability-reporting"):
            return {"enabled": False}
        if path.endswith("/releases/latest"):
            return None
        if path.endswith("/tags"):
            return []
        raise AssertionError(f"unexpected path: {path}")

    async def exists(self, path: str) -> bool:
        return False


@pytest.mark.asyncio
async def test_uses_collected_candidates_when_later_search_query_is_rate_limited():
    service = GitHubCveDiscoveryService(
        client=SearchRateLimitedAfterFirstPageClient(),
        now_provider=lambda: datetime(2026, 6, 6, tzinfo=timezone.utc),
    )

    candidates = await service.discover_candidates(target_count=1)

    assert [candidate.full_name for candidate in candidates] == ["first/result"]


class VeryPopularProjectClient:
    async def get_json(self, path: str, params: dict | None = None):
        if path == "/search/repositories":
            return {
                "items": [
                    {
                        "full_name": "very/popular",
                        "html_url": "https://github.com/very/popular",
                        "description": "Self-hosted CMS dashboard",
                        "stargazers_count": 150000,
                        "pushed_at": "2026-05-20T12:00:00Z",
                        "updated_at": "2026-05-20T12:00:00Z",
                        "archived": False,
                        "fork": False,
                        "default_branch": "main",
                        "language": "TypeScript",
                    },
                    {
                        "full_name": "focused/cms",
                        "html_url": "https://github.com/focused/cms",
                        "description": "Self-hosted CMS dashboard",
                        "stargazers_count": 12000,
                        "pushed_at": "2026-05-20T12:00:00Z",
                        "updated_at": "2026-05-20T12:00:00Z",
                        "archived": False,
                        "fork": False,
                        "default_branch": "main",
                        "language": "TypeScript",
                    },
                ]
            }
        if path.endswith("/security-advisories"):
            return []
        if path.endswith("/private-vulnerability-reporting"):
            return {"enabled": False}
        if path.endswith("/releases/latest"):
            return None
        if path.endswith("/tags"):
            return []
        raise AssertionError(f"unexpected path: {path}")

    async def exists(self, path: str) -> bool:
        return False


@pytest.mark.asyncio
async def test_deprioritizes_repositories_above_one_hundred_thousand_stars():
    service = GitHubCveDiscoveryService(
        client=VeryPopularProjectClient(),
        now_provider=lambda: datetime(2026, 6, 6, tzinfo=timezone.utc),
    )

    candidates = await service.discover_candidates(target_count=2)

    assert [candidate.full_name for candidate in candidates] == ["focused/cms", "very/popular"]


class TimeoutDuringEnrichmentClient:
    async def get_json(self, path: str, params: dict | None = None):
        if path == "/search/repositories":
            return {
                "items": [
                    {
                        "full_name": "timeout/cms",
                        "html_url": "https://github.com/timeout/cms",
                        "description": "Self-hosted CMS dashboard",
                        "stargazers_count": 3200,
                        "pushed_at": "2026-05-20T12:00:00Z",
                        "updated_at": "2026-05-20T12:00:00Z",
                        "archived": False,
                        "fork": False,
                        "default_branch": "main",
                        "language": "TypeScript",
                    }
                ]
            }
        raise httpx.ReadTimeout("github timed out")

    async def exists(self, path: str) -> bool:
        raise httpx.ReadTimeout("github timed out")


@pytest.mark.asyncio
async def test_discovers_candidates_when_enrichment_requests_timeout():
    service = GitHubCveDiscoveryService(
        client=TimeoutDuringEnrichmentClient(),
        now_provider=lambda: datetime(2026, 6, 6, tzinfo=timezone.utc),
    )

    candidates = await service.discover_candidates(target_count=1)

    assert [candidate.full_name for candidate in candidates] == ["timeout/cms"]
    assert candidates[0].has_security_advisory is False
    assert candidates[0].has_security_policy is False
    assert candidates[0].version_label == "main"
    assert candidates[0].version_source == "default_branch"


class ManyRepositoryClient:
    def __init__(self):
        self.enrichment_paths: list[str] = []

    async def get_json(self, path: str, params: dict | None = None):
        if path == "/search/repositories":
            return {
                "items": [
                    {
                        "full_name": f"candidate/project-{index}",
                        "html_url": f"https://github.com/candidate/project-{index}",
                        "description": "Self-hosted CMS dashboard",
                        "stargazers_count": 5000 + index,
                        "pushed_at": "2026-05-20T12:00:00Z",
                        "updated_at": "2026-05-20T12:00:00Z",
                        "archived": False,
                        "fork": False,
                        "default_branch": "main",
                        "language": "TypeScript",
                    }
                    for index in range(30)
                ]
            }
        self.enrichment_paths.append(path)
        if path.endswith("/security-advisories"):
            return []
        if path.endswith("/private-vulnerability-reporting"):
            return {"enabled": False}
        if path.endswith("/releases/latest"):
            return None
        if path.endswith("/tags"):
            return []
        raise AssertionError(f"unexpected path: {path}")

    async def exists(self, path: str) -> bool:
        self.enrichment_paths.append(path)
        return False


@pytest.mark.asyncio
async def test_enriches_only_requested_number_of_top_candidates():
    client = ManyRepositoryClient()
    service = GitHubCveDiscoveryService(
        client=client,
        now_provider=lambda: datetime(2026, 6, 6, tzinfo=timezone.utc),
    )

    candidates = await service.discover_candidates(target_count=5)

    assert len(candidates) == 5
    assert all(candidate.full_name.startswith("candidate/project-") for candidate in candidates)
    assert len([path for path in client.enrichment_paths if path.endswith("/security-advisories")]) == 15


class ReportableAdvisoryClient:
    async def get_json(self, path: str, params: dict | None = None):
        if path == "/search/repositories":
            return {
                "items": [
                    {
                        "full_name": "advisory/only",
                        "html_url": "https://github.com/advisory/only",
                        "description": "Self-hosted CMS dashboard",
                        "stargazers_count": 90000,
                        "pushed_at": "2026-05-20T12:00:00Z",
                        "updated_at": "2026-05-20T12:00:00Z",
                        "archived": False,
                        "fork": False,
                        "default_branch": "main",
                        "language": "TypeScript",
                    },
                    {
                        "full_name": "reportable/cms",
                        "html_url": "https://github.com/reportable/cms",
                        "description": "Self-hosted CMS dashboard",
                        "stargazers_count": 1500,
                        "pushed_at": "2026-05-20T12:00:00Z",
                        "updated_at": "2026-05-20T12:00:00Z",
                        "archived": False,
                        "fork": False,
                        "default_branch": "main",
                        "language": "TypeScript",
                    },
                ]
            }
        if path == "/repos/advisory/only/security-advisories":
            return [{"ghsa_id": "GHSA-advisory-only"}]
        if path == "/repos/reportable/cms/security-advisories":
            return [{"ghsa_id": "GHSA-reportable-cms"}]
        if path == "/repos/reportable/cms/private-vulnerability-reporting":
            return {"enabled": True}
        if path.endswith("/private-vulnerability-reporting"):
            return {"enabled": False}
        if path.endswith("/releases/latest"):
            return None
        if path.endswith("/tags"):
            return []
        raise AssertionError(f"unexpected path: {path}")

    async def exists(self, path: str) -> bool:
        return False


@pytest.mark.asyncio
async def test_prioritizes_advisory_projects_with_private_vulnerability_reporting():
    service = GitHubCveDiscoveryService(
        client=ReportableAdvisoryClient(),
        now_provider=lambda: datetime(2026, 6, 6, tzinfo=timezone.utc),
    )

    candidates = await service.discover_candidates(target_count=2)

    assert [candidate.full_name for candidate in candidates] == ["reportable/cms", "advisory/only"]
    assert candidates[0].has_security_advisory is True
    assert candidates[0].has_private_vulnerability_reporting is True


class NoAdvisoryPreferenceClient:
    def __init__(self):
        self.calls: list[str] = []
        self.queries: list[str] = []

    async def get_json(self, path: str, params: dict | None = None):
        self.calls.append(path)
        if path == "/search/repositories":
            self.queries.append(str((params or {}).get("q") or ""))
            return {
                "items": [
                    {
                        "full_name": "too/popular",
                        "html_url": "https://github.com/too/popular",
                        "description": "Self-hosted CMS dashboard",
                        "stargazers_count": 150000,
                        "pushed_at": "2026-05-20T12:00:00Z",
                        "updated_at": "2026-05-20T12:00:00Z",
                        "archived": False,
                        "fork": False,
                        "default_branch": "main",
                        "language": "TypeScript",
                    },
                    {
                        "full_name": "normal/cms",
                        "html_url": "https://github.com/normal/cms",
                        "description": "Self-hosted CMS dashboard",
                        "stargazers_count": 12000,
                        "pushed_at": "2026-05-20T12:00:00Z",
                        "updated_at": "2026-05-20T12:00:00Z",
                        "archived": False,
                        "fork": False,
                        "default_branch": "main",
                        "language": "TypeScript",
                    },
                ]
            }
        if path.endswith("/releases/latest"):
            return None
        if path.endswith("/tags"):
            return []
        raise AssertionError(f"unexpected path: {path}")

    async def exists(self, path: str) -> bool:
        raise AssertionError(f"unexpected exists path: {path}")


@pytest.mark.asyncio
async def test_can_disable_security_advisory_priority_and_use_normal_star_sorting():
    client = NoAdvisoryPreferenceClient()
    service = GitHubCveDiscoveryService(
        client=client,
        now_provider=lambda: datetime(2026, 6, 6, tzinfo=timezone.utc),
    )

    candidates = await service.discover_candidates(target_count=2, prefer_security_advisory=False)

    assert [candidate.full_name for candidate in candidates] == ["normal/cms", "too/popular"]
    assert any("stars:>1000" in query for query in client.queries)
    assert not any(path.endswith("/security-advisories") for path in client.calls)
    assert not any(path.endswith("/private-vulnerability-reporting") for path in client.calls)


class StarBandPriorityClient:
    async def get_json(self, path: str, params: dict | None = None):
        if path == "/search/repositories":
            return {
                "items": [
                    {
                        "full_name": "popular/project",
                        "html_url": "https://github.com/popular/project",
                        "description": "Self-hosted CMS dashboard",
                        "stargazers_count": 120000,
                        "pushed_at": "2026-05-20T12:00:00Z",
                        "updated_at": "2026-05-20T12:00:00Z",
                        "archived": False,
                        "fork": False,
                        "default_branch": "main",
                        "language": "TypeScript",
                    },
                    {
                        "full_name": "large/project",
                        "html_url": "https://github.com/large/project",
                        "description": "Self-hosted CMS dashboard",
                        "stargazers_count": 70000,
                        "pushed_at": "2026-05-20T12:00:00Z",
                        "updated_at": "2026-05-20T12:00:00Z",
                        "archived": False,
                        "fork": False,
                        "default_branch": "main",
                        "language": "TypeScript",
                    },
                    {
                        "full_name": "medium/project",
                        "html_url": "https://github.com/medium/project",
                        "description": "Self-hosted CMS dashboard",
                        "stargazers_count": 40000,
                        "pushed_at": "2026-05-20T12:00:00Z",
                        "updated_at": "2026-05-20T12:00:00Z",
                        "archived": False,
                        "fork": False,
                        "default_branch": "main",
                        "language": "TypeScript",
                    },
                    {
                        "full_name": "focused/project",
                        "html_url": "https://github.com/focused/project",
                        "description": "Self-hosted CMS dashboard",
                        "stargazers_count": 12000,
                        "pushed_at": "2026-05-20T12:00:00Z",
                        "updated_at": "2026-05-20T12:00:00Z",
                        "archived": False,
                        "fork": False,
                        "default_branch": "main",
                        "language": "TypeScript",
                    },
                ]
            }
        if path.endswith("/releases/latest"):
            return None
        if path.endswith("/tags"):
            return []
        raise AssertionError(f"unexpected path: {path}")

    async def exists(self, path: str) -> bool:
        raise AssertionError(f"unexpected exists path: {path}")


@pytest.mark.asyncio
async def test_prioritizes_star_bands_when_security_advisory_priority_is_disabled():
    service = GitHubCveDiscoveryService(
        client=StarBandPriorityClient(),
        now_provider=lambda: datetime(2026, 6, 6, tzinfo=timezone.utc),
    )

    candidates = await service.discover_candidates(target_count=4, prefer_security_advisory=False)

    assert [candidate.full_name for candidate in candidates] == [
        "focused/project",
        "medium/project",
        "large/project",
        "popular/project",
    ]


class PublishedGhsaCountClient:
    async def get_json(self, path: str, params: dict | None = None):
        if path == "/search/repositories":
            return {
                "items": [
                    {
                        "full_name": "single/advisory",
                        "html_url": "https://github.com/single/advisory",
                        "description": "Self-hosted CMS dashboard",
                        "stargazers_count": 12000,
                        "pushed_at": "2026-05-20T12:00:00Z",
                        "updated_at": "2026-05-20T12:00:00Z",
                        "archived": False,
                        "fork": False,
                        "default_branch": "main",
                        "language": "TypeScript",
                    },
                    {
                        "full_name": "many/advisories",
                        "html_url": "https://github.com/many/advisories",
                        "description": "Self-hosted CMS dashboard",
                        "stargazers_count": 40000,
                        "pushed_at": "2026-05-20T12:00:00Z",
                        "updated_at": "2026-05-20T12:00:00Z",
                        "archived": False,
                        "fork": False,
                        "default_branch": "main",
                        "language": "TypeScript",
                    },
                ]
            }
        if path == "/repos/single/advisory/security-advisories":
            return [{"ghsa_id": "GHSA-one"}]
        if path == "/repos/many/advisories/security-advisories":
            return [{"ghsa_id": f"GHSA-{index}"} for index in range(6)]
        if path.endswith("/private-vulnerability-reporting"):
            return {"enabled": False}
        if path.endswith("/releases/latest"):
            return None
        if path.endswith("/tags"):
            return []
        raise AssertionError(f"unexpected path: {path}")

    async def exists(self, path: str) -> bool:
        return False


@pytest.mark.asyncio
async def test_published_ghsa_count_adds_priority_when_security_advisory_priority_is_enabled():
    service = GitHubCveDiscoveryService(
        client=PublishedGhsaCountClient(),
        now_provider=lambda: datetime(2026, 6, 6, tzinfo=timezone.utc),
    )

    candidates = await service.discover_candidates(target_count=2)

    assert [candidate.full_name for candidate in candidates] == ["many/advisories", "single/advisory"]
    assert candidates[0].advisory_count == 6
    assert candidates[1].advisory_count == 1
