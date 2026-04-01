import json
from types import SimpleNamespace

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
