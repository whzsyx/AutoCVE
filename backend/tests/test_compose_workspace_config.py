from __future__ import annotations

from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
PROJECT_VOLUME = "./projects:/workspace/projects"
SKILL_LIBRARY_TARGET = "/app/skill_library"
REPORT_TEMPLATE_TARGET = "/app/report_template_library"
MANAGED_ROOT = "MANAGED_PROJECTS_ROOT"
MANAGED_ROOT_VALUE = "/workspace/projects"
PROJECT_SOURCE_ROOT = "PROJECT_SOURCE_STORAGE_PATH"
PROJECT_SOURCE_ROOT_VALUE = "/app/uploads/project_sources"
PROJECT_SERVICES = ("backend", "agent-worker", "one-click-cve-worker")


def _service_block(compose_text: str, service_name: str) -> str:
    marker = f"  {service_name}:\n"
    start = compose_text.index(marker)
    next_service = compose_text.find("\n  ", start + len(marker))
    while next_service != -1 and compose_text[next_service + 3 : next_service + 4] == " ":
        next_service = compose_text.find("\n  ", next_service + 1)
    if next_service == -1:
        volumes = compose_text.find("\nvolumes:", start)
        return compose_text[start:volumes]
    return compose_text[start:next_service]


def _assert_service_has_managed_workspace(compose_text: str, service_name: str) -> None:
    block = _service_block(compose_text, service_name)
    assert PROJECT_VOLUME in block
    assert SKILL_LIBRARY_TARGET in block
    assert REPORT_TEMPLATE_TARGET in block
    assert MANAGED_ROOT in block
    assert MANAGED_ROOT_VALUE in block
    assert PROJECT_SOURCE_ROOT in block
    assert PROJECT_SOURCE_ROOT_VALUE in block


def test_prod_compose_services_share_managed_project_workspace():
    compose_text = (REPO_ROOT / "docker-compose.prod.yml").read_text(encoding="utf-8")

    for service_name in PROJECT_SERVICES:
        _assert_service_has_managed_workspace(compose_text, service_name)


def test_prod_cn_compose_services_share_managed_project_workspace():
    compose_text = (REPO_ROOT / "docker-compose.prod.cn.yml").read_text(encoding="utf-8")

    for service_name in PROJECT_SERVICES:
        _assert_service_has_managed_workspace(compose_text, service_name)
