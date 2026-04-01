# Workflow Management Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a persistent workflow-management tab under model management that visualizes the DeepAudit agent pipeline and lets users enable or disable runtime agents, with the backend execution path automatically skipping disabled nodes.

**Architecture:** Extend the existing user config payload with a workflow configuration stored in `otherConfig`, then teach the orchestrator/runtime bootstrap code to derive an effective route from enabled agents before dispatch. Reuse the current admin config APIs for persistence, and build a new frontend tab that renders a polished, dynamic graph plus toggle cards from the saved workflow config.

**Tech Stack:** FastAPI, Pydantic, SQLAlchemy-backed user config storage, existing agent orchestrator runtime, React, TypeScript, shadcn/ui, lucide-react.

---

## Chunk 1: Backend Workflow Config Contract

### Task 1: Add failing tests for workflow defaults and merge behavior

**Files:**
- Create: `backend/tests/api/test_workflow_config.py`
- Modify: `backend/app/api/v1/endpoints/config.py`

- [ ] **Step 1: Write failing tests for default workflow config shape**
- [ ] **Step 2: Run targeted pytest to verify the tests fail**
- [ ] **Step 3: Add workflow config schema/default merge helpers under `otherConfig`**
- [ ] **Step 4: Re-run targeted pytest to verify the tests pass**

### Task 2: Add failing tests for effective route derivation

**Files:**
- Modify: `backend/tests/agent/test_agent_contracts.py`
- Modify: `backend/app/services/agent/agents/orchestrator.py`

- [ ] **Step 1: Write failing tests for enabled/disabled route computation**
- [ ] **Step 2: Run targeted pytest to verify the new tests fail**
- [ ] **Step 3: Add workflow route derivation helpers and validation rules**
- [ ] **Step 4: Re-run targeted pytest to verify the tests pass**

## Chunk 2: Backend Runtime Execution

### Task 3: Make task execution consume persisted workflow config

**Files:**
- Modify: `backend/app/api/v1/endpoints/agent_tasks.py`
- Modify: `backend/app/api/v1/endpoints/config.py`

- [ ] **Step 1: Thread merged workflow config into the agent task runtime input**
- [ ] **Step 2: Preserve the config in task execution metadata passed to the orchestrator**
- [ ] **Step 3: Keep agent model override logic intact while adding workflow settings**
- [ ] **Step 4: Re-run targeted backend tests**

### Task 4: Update orchestrator execution semantics to auto-skip disabled nodes

**Files:**
- Modify: `backend/app/services/agent/agents/orchestrator.py`
- Modify: `backend/tests/agent/test_agent_contracts.py`

- [ ] **Step 1: Add failing tests for scan/triage/verification disabled scenarios**
- [ ] **Step 2: Run targeted pytest and confirm failures**
- [ ] **Step 3: Execute only enabled agents, merge findings from enabled branches, and omit disabled phase payloads**
- [ ] **Step 4: Re-run targeted pytest and confirm pass**

## Chunk 3: Frontend Workflow Management UI

### Task 5: Extend shared config API types for workflow management

**Files:**
- Modify: `frontend/src/shared/api/modelConfig.ts`
- Modify: `frontend/src/components/system/SystemConfig.tsx`

- [ ] **Step 1: Add failing type-level/build references for workflow config types**
- [ ] **Step 2: Run a frontend type/build check to verify failure**
- [ ] **Step 3: Add workflow config types and load/save wiring via `/config/me`**
- [ ] **Step 4: Re-run the type/build check**

### Task 6: Add the workflow-management tab and dynamic visual graph

**Files:**
- Modify: `frontend/src/pages/AdminDashboard.tsx`
- Create: `frontend/src/components/system/WorkflowManager.tsx`

- [ ] **Step 1: Add a failing build check for the new tab/component import**
- [ ] **Step 2: Run the frontend type/build check and confirm failure**
- [ ] **Step 3: Implement the new tab with styled agent cards, toggles, effective route summary, and responsive SVG/DOM workflow graph**
- [ ] **Step 4: Re-run the frontend type/build check and confirm pass**

## Chunk 4: Final Verification

### Task 7: Verify end-to-end behavior and summarize constraints

**Files:**
- Modify: `backend/app/services/agent/agents/orchestrator.py`
- Modify: `frontend/src/components/system/WorkflowManager.tsx`

- [ ] **Step 1: Re-read the requested workflow semantics and compare against the final code path**
- [ ] **Step 2: Run targeted backend pytest commands**
- [ ] **Step 3: Run the frontend verification command**
- [ ] **Step 4: Report the verified behavior, including any guardrails such as always-on orchestrator/recon**
