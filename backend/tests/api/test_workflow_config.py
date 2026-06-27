import json
from types import SimpleNamespace

from app.api.v1.endpoints.agent_tasks import _merge_task_workflow_config
from app.api.v1.endpoints.config import get_default_config, _merge_user_config


def test_default_config_exposes_workflow_management_defaults():
    config = get_default_config()

    workflow = config["otherConfig"]["workflowConfig"]

    assert workflow["agentStates"]["orchestrator"]["enabled"] is True
    assert workflow["agentStates"]["recon"]["enabled"] is True
    assert workflow["agentStates"]["scan"]["enabled"] is True
    assert workflow["agentStates"]["triage"]["enabled"] is True
    assert workflow["agentStates"]["finding"]["enabled"] is True
    assert workflow["agentStates"]["verification"]["enabled"] is True


def test_merge_user_config_backfills_missing_workflow_agents():
    record = SimpleNamespace(
        llm_config=json.dumps({}),
        other_config=json.dumps(
            {
                "workflowConfig": {
                    "agentStates": {
                        "scan": {"enabled": False},
                        "finding": {"enabled": False},
                    }
                }
            }
        ),
    )

    merged = _merge_user_config(record)
    workflow = merged["otherConfig"]["workflowConfig"]

    assert workflow["agentStates"]["orchestrator"]["enabled"] is True
    assert workflow["agentStates"]["recon"]["enabled"] is True
    assert workflow["agentStates"]["scan"]["enabled"] is False
    assert workflow["agentStates"]["triage"]["enabled"] is True
    assert workflow["agentStates"]["finding"]["enabled"] is False
    assert workflow["agentStates"]["verification"]["enabled"] is True


def test_task_audit_scope_workflow_overrides_global_workflow():
    global_workflow = {
        "agentStates": {
            "orchestrator": {"enabled": True},
            "recon": {"enabled": True},
            "scan": {"enabled": False},
            "triage": {"enabled": True},
            "finding": {"enabled": True},
            "verification": {"enabled": True},
        }
    }
    audit_scope = {
        "workflow": {
            "agentStates": {
                "scan": {"enabled": True},
                "triage": {"enabled": True},
                "finding": {"enabled": False},
                "verification": {"enabled": False},
            }
        }
    }

    merged = _merge_task_workflow_config(global_workflow, audit_scope)

    assert merged["agentStates"]["orchestrator"]["enabled"] is True
    assert merged["agentStates"]["recon"]["enabled"] is True
    assert merged["agentStates"]["scan"]["enabled"] is True
    assert merged["agentStates"]["triage"]["enabled"] is True
    assert merged["agentStates"]["finding"]["enabled"] is False
    assert merged["agentStates"]["verification"]["enabled"] is False


def test_task_audit_scope_keeps_unset_agents_from_global_workflow():
    global_workflow = {
        "agentStates": {
            "orchestrator": {"enabled": True},
            "recon": {"enabled": True},
            "scan": {"enabled": True},
            "triage": {"enabled": False},
            "finding": {"enabled": True},
            "verification": {"enabled": True},
        }
    }
    audit_scope = {
        "workflow": {
            "agentStates": {
                "finding": {"enabled": False},
            }
        }
    }

    merged = _merge_task_workflow_config(global_workflow, audit_scope)

    assert merged["agentStates"]["scan"]["enabled"] is True
    assert merged["agentStates"]["triage"]["enabled"] is False
    assert merged["agentStates"]["finding"]["enabled"] is False
    assert merged["agentStates"]["verification"]["enabled"] is True


def test_one_click_cve_workflow_disables_expensive_agents_without_global_opt_in():
    merged = _merge_task_workflow_config(
        {},
        {
            "one_click_cve": {
                "batch_id": "batch-1",
                "github_full_name": "owner/repo",
            }
        },
    )

    assert merged["agentStates"]["orchestrator"]["enabled"] is True
    assert merged["agentStates"]["recon"]["enabled"] is True
    assert merged["agentStates"]["finding"]["enabled"] is True
    assert merged["agentStates"]["scan"]["enabled"] is False
    assert merged["agentStates"]["triage"]["enabled"] is False
    assert merged["agentStates"]["verification"]["enabled"] is False


def test_one_click_cve_workflow_ignores_global_expensive_agent_opt_in():
    global_workflow = {
        "agentStates": {
            "scan": {"enabled": True},
            "triage": {"enabled": True},
            "finding": {"enabled": False},
            "verification": {"enabled": True},
        }
    }

    merged = _merge_task_workflow_config(
        global_workflow,
        {
            "one_click_cve": {
                "batch_id": "batch-1",
                "github_full_name": "owner/repo",
            }
        },
    )

    assert merged["agentStates"]["recon"]["enabled"] is True
    assert merged["agentStates"]["finding"]["enabled"] is True
    assert merged["agentStates"]["scan"]["enabled"] is False
    assert merged["agentStates"]["triage"]["enabled"] is False
    assert merged["agentStates"]["verification"]["enabled"] is False


def test_one_click_cve_workflow_ignores_incomplete_global_expensive_agent_states():
    global_workflow = {
        "agentStates": {
            "scan": {},
            "triage": {"locked": False},
            "verification": {"enabled": False},
        }
    }

    merged = _merge_task_workflow_config(
        global_workflow,
        {
            "one_click_cve": {
                "batch_id": "batch-1",
                "github_full_name": "owner/repo",
            }
        },
    )

    assert merged["agentStates"]["scan"]["enabled"] is False
    assert merged["agentStates"]["triage"]["enabled"] is False
    assert merged["agentStates"]["verification"]["enabled"] is False
