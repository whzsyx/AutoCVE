# Finding Agent V2 Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Rebuild the finding agent around a serial controller-worker workflow with preloaded skill context, structured evidence, candidate queues, and batched tool execution while preserving the existing findings output contract.

**Architecture:** Keep the public `FindingAgent` entrypoint stable, but move its internals to a V2 runtime composed of a controller, coverage/candidate/evidence helpers, and sequential candidate workers. Extend the existing workflow parser/executor to support `Action Batch` so workers can perform small batches of tool calls per reasoning turn without falling back to a single giant ReAct loop.

**Tech Stack:** Python 3.12, pytest, existing DeepAudit agent/tool runtime

---

## Chunk 1: Workflow Engine Upgrades

### Task 1: Add batch tool-call parsing support

**Files:**
- Modify: `backend/app/services/agent/agents/analysis_workflow.py`
- Test: `backend/tests/agent/test_agent_contracts.py`

- [ ] **Step 1: Write a failing test for `Action Batch` parsing**
- [ ] **Step 2: Run `pytest backend/tests/agent/test_agent_contracts.py -k action_batch -v` and verify it fails**
- [ ] **Step 3: Extend `WorkflowStep` and `_parse_llm_response()` to support `Action Batch` JSON arrays**
- [ ] **Step 4: Run the same test and verify it passes**

### Task 2: Add batched tool execution support

**Files:**
- Modify: `backend/app/services/agent/agents/analysis_workflow.py`
- Test: `backend/tests/agent/test_agent_contracts.py`

- [ ] **Step 1: Write a failing test that a workflow step with multiple actions executes them sequentially and aggregates observations**
- [ ] **Step 2: Run `pytest backend/tests/agent/test_agent_contracts.py -k batch_execution -v` and verify it fails**
- [ ] **Step 3: Implement aggregated batch execution with conservative limits**
- [ ] **Step 4: Re-run the targeted test and verify it passes**

## Chunk 2: Finding V2 Runtime Helpers

### Task 3: Add preloaded skill context support

**Files:**
- Create: `backend/app/services/agent/agents/finding_skill_preloader.py`
- Modify: `backend/app/services/agent/agents/finding.py`
- Test: `backend/tests/agent/test_agent_contracts.py`

- [ ] **Step 1: Write a failing test that finding V2 can preload the primary skill body and mandatory resources without requiring runtime skill tool calls**
- [ ] **Step 2: Run `pytest backend/tests/agent/test_agent_contracts.py -k preloaded_skill -v` and verify it fails**
- [ ] **Step 3: Implement `FindingSkillPreloader` and wire it into finding runtime context creation**
- [ ] **Step 4: Re-run the targeted test and verify it passes**

### Task 4: Add coverage, candidate, and evidence models

**Files:**
- Create: `backend/app/services/agent/agents/finding_coverage.py`
- Create: `backend/app/services/agent/agents/finding_candidates.py`
- Create: `backend/app/services/agent/agents/finding_evidence.py`
- Test: `backend/tests/agent/test_agent_contracts.py`

- [ ] **Step 1: Write failing tests for candidate generation, de-duplication, and evidence bundle recording**
- [ ] **Step 2: Run `pytest backend/tests/agent/test_agent_contracts.py -k 'candidate_queue or evidence_bundle' -v` and verify failures**
- [ ] **Step 3: Implement the data classes and helper logic**
- [ ] **Step 4: Re-run the targeted tests and verify they pass**

## Chunk 3: Finding Controller and Worker Flow

### Task 5: Add a serial controller-worker orchestrator

**Files:**
- Create: `backend/app/services/agent/agents/finding_controller.py`
- Create: `backend/app/services/agent/agents/finding_worker.py`
- Create: `backend/app/services/agent/agents/finding_synthesizer.py`
- Modify: `backend/app/services/agent/agents/finding.py`
- Test: `backend/tests/agent/test_agent_contracts.py`

- [ ] **Step 1: Write failing tests for the serial controller-worker flow using monkeypatched LLM responses**
- [ ] **Step 2: Run `pytest backend/tests/agent/test_agent_contracts.py -k finding_v2 -v` and verify they fail**
- [ ] **Step 3: Implement controller planning, candidate dispatch, worker execution, and synthesized output**
- [ ] **Step 4: Re-run the targeted tests and verify they pass**

### Task 6: Add loop detection for no-progress candidate exploration

**Files:**
- Create: `backend/app/services/agent/agents/finding_loop_detector.py`
- Modify: `backend/app/services/agent/agents/finding_worker.py`
- Test: `backend/tests/agent/test_agent_contracts.py`

- [ ] **Step 1: Write a failing test for repeated no-progress tool patterns in candidate workers**
- [ ] **Step 2: Run `pytest backend/tests/agent/test_agent_contracts.py -k loop_detector -v` and verify it fails**
- [ ] **Step 3: Implement lightweight repeat/ping-pong detection and worker guidance/blocking**
- [ ] **Step 4: Re-run the targeted test and verify it passes**

## Chunk 4: Integration and Verification

### Task 7: Preserve output and handoff compatibility

**Files:**
- Modify: `backend/app/services/agent/agents/finding.py`
- Modify: `backend/app/services/agent/agents/analysis_workflow.py`
- Test: `backend/tests/agent/test_agent_contracts.py`

- [ ] **Step 1: Write failing tests asserting V2 still returns normalized findings and verification handoff context**
- [ ] **Step 2: Run `pytest backend/tests/agent/test_agent_contracts.py -k 'handoff or normalized findings' -v` and verify failures if behavior changed**
- [ ] **Step 3: Adjust synthesis/postprocessing to preserve existing contracts**
- [ ] **Step 4: Re-run the targeted tests and verify they pass**

### Task 8: Run focused verification

**Files:**
- Test: `backend/tests/agent/test_agent_contracts.py`
- Test: `backend/tests/agent/test_agents.py`

- [ ] **Step 1: Run `pytest backend/tests/agent/test_agent_contracts.py -v`**
- [ ] **Step 2: Run `pytest backend/tests/agent/test_agents.py -v`**
- [ ] **Step 3: Fix regressions until both commands pass or document blockers**
