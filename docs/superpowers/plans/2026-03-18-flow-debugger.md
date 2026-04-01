# Flow Debugger Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a new standalone `流程调试` page that lets users pick an audit task and inspect the full debug trace for every agent, including prompts, ReAct steps, tool I/O, and handoff payloads.

**Architecture:** Extend the existing agent event pipeline with a debug-trace event model and retrieval API, then build a dedicated frontend debugger page that visualizes task selection, grouped timeline events, and full event payload details. Reuse current event storage and streaming patterns rather than creating a parallel runtime.

**Tech Stack:** FastAPI, SQLAlchemy, existing agent event manager, React, TypeScript, current DeepAudit UI component library.

---

## Chunk 1: Backend Debug Event Protocol

### Task 1: Add backend tests for debug event serialization helpers

**Files:**
- Create: `D:\文件\pythonProject\AICVE\AutoCVE\DeepAudit-3.0.0\backend\tests\agent\test_debug_trace.py`
- Modify: `D:\文件\pythonProject\AICVE\AutoCVE\DeepAudit-3.0.0\backend\app\services\agent\event_manager.py`

- [ ] **Step 1: Write failing tests for debug event payload shape**
- [ ] **Step 2: Run targeted pytest to verify the new tests fail**
- [ ] **Step 3: Add event manager helpers for prompt, react, tool I/O, and handoff debug events**
- [ ] **Step 4: Re-run targeted pytest to verify those tests pass**

### Task 2: Add debug-trace API contract tests

**Files:**
- Modify: `D:\文件\pythonProject\AICVE\AutoCVE\DeepAudit-3.0.0\backend\tests\agent\test_debug_trace.py`
- Modify: `D:\文件\pythonProject\AICVE\AutoCVE\DeepAudit-3.0.0\backend\app\api\v1\endpoints\agent_tasks.py`
- Modify: `D:\文件\pythonProject\AICVE\AutoCVE\DeepAudit-3.0.0\backend\app\models\agent_task.py`

- [ ] **Step 1: Write failing tests for task list and per-task debug trace responses**
- [ ] **Step 2: Run the targeted pytest cases and confirm the API tests fail for missing endpoints**
- [ ] **Step 3: Implement `GET /api/v1/agent-tasks/debug-tasks` and `GET /api/v1/agent-tasks/{task_id}/debug-trace`**
- [ ] **Step 4: Re-run the targeted pytest cases and confirm they pass**

## Chunk 2: Agent Runtime Instrumentation

### Task 3: Instrument analysis workflow agents with debug prompt and ReAct events

**Files:**
- Modify: `D:\文件\pythonProject\AICVE\AutoCVE\DeepAudit-3.0.0\backend\app\services\agent\agents\analysis_workflow.py`
- Modify: `D:\文件\pythonProject\AICVE\AutoCVE\DeepAudit-3.0.0\backend\app\services\agent\agents\base.py`

- [ ] **Step 1: Write failing tests or lightweight assertions for analysis workflow debug event emission order**
- [ ] **Step 2: Confirm the new checks fail before implementation**
- [ ] **Step 3: Emit `agent_start`, `prompt_system`, `prompt_user`, `react_thought`, `react_action`, `react_observation`, and raw model response events**
- [ ] **Step 4: Re-run the checks and confirm they pass**

### Task 4: Instrument recon, orchestrator, and verification with prompt and handoff tracing

**Files:**
- Modify: `D:\文件\pythonProject\AICVE\AutoCVE\DeepAudit-3.0.0\backend\app\services\agent\agents\recon.py`
- Modify: `D:\文件\pythonProject\AICVE\AutoCVE\DeepAudit-3.0.0\backend\app\services\agent\agents\orchestrator.py`
- Modify: `D:\文件\pythonProject\AICVE\AutoCVE\DeepAudit-3.0.0\backend\app\services\agent\agents\verification.py`

- [ ] **Step 1: Add failing coverage for handoff and prompt events in these agents**
- [ ] **Step 2: Verify the coverage fails before implementation**
- [ ] **Step 3: Emit `handoff_out` and `handoff_in` with full payloads, plus prompt/debug events in recon and verification**
- [ ] **Step 4: Re-run targeted tests and confirm they pass**

## Chunk 3: Model Test Alignment

### Task 5: Align model testing endpoint with real debug event prompt structure

**Files:**
- Modify: `D:\文件\pythonProject\AICVE\AutoCVE\DeepAudit-3.0.0\backend\app\api\v1\endpoints\config.py`

- [ ] **Step 1: Write failing assertions for model-test debug payload shape**
- [ ] **Step 2: Verify the test fails**
- [ ] **Step 3: Make test-agent-model expose the same prompt/debug structure as real agent startup**
- [ ] **Step 4: Re-run validation and confirm it passes**

## Chunk 4: Frontend Debugger UI

### Task 6: Add frontend API types and route wiring for flow debugger

**Files:**
- Modify: `D:\文件\pythonProject\AICVE\AutoCVE\DeepAudit-3.0.0\frontend\src\app\routes.tsx`
- Modify: `D:\文件\pythonProject\AICVE\AutoCVE\DeepAudit-3.0.0\frontend\src\components\layout\Sidebar.tsx`
- Create: `D:\文件\pythonProject\AICVE\AutoCVE\DeepAudit-3.0.0\frontend\src\shared\api\flowDebugger.ts`

- [ ] **Step 1: Add failing frontend type-level or build-level checks for the new API module and route**
- [ ] **Step 2: Verify the build fails without the implementation**
- [ ] **Step 3: Add route, menu entry, and API client functions for debug tasks and debug trace**
- [ ] **Step 4: Re-run the build or TypeScript check and confirm it passes**

### Task 7: Build the standalone Flow Debugger page

**Files:**
- Create: `D:\文件\pythonProject\AICVE\AutoCVE\DeepAudit-3.0.0\frontend\src\pages\FlowDebugger.tsx`
- Create: `D:\文件\pythonProject\AICVE\AutoCVE\DeepAudit-3.0.0\frontend\src\pages\flow-debugger\components\DebugTaskSelector.tsx`
- Create: `D:\文件\pythonProject\AICVE\AutoCVE\DeepAudit-3.0.0\frontend\src\pages\flow-debugger\components\DebugTimeline.tsx`
- Create: `D:\文件\pythonProject\AICVE\AutoCVE\DeepAudit-3.0.0\frontend\src\pages\flow-debugger\components\DebugDetailPanel.tsx`
- Create: `D:\文件\pythonProject\AICVE\AutoCVE\DeepAudit-3.0.0\frontend\src\pages\flow-debugger\components\DebugFlowGraph.tsx`

- [ ] **Step 1: Add a failing build check for the new page shell**
- [ ] **Step 2: Confirm the build fails before implementation**
- [ ] **Step 3: Implement the task selector, grouped timeline, event detail panel, and handoff graph**
- [ ] **Step 4: Re-run the build and confirm it passes**

## Chunk 5: Final Integration and Verification

### Task 8: Wire detail payloads into existing event storage and verify end-to-end behavior

**Files:**
- Modify: `D:\文件\pythonProject\AICVE\AutoCVE\DeepAudit-3.0.0\backend\app\api\v1\endpoints\agent_tasks.py`
- Modify: `D:\文件\pythonProject\AICVE\AutoCVE\DeepAudit-3.0.0\backend\app\services\agent\streaming\stream_handler.py`
- Modify: `D:\文件\pythonProject\AICVE\AutoCVE\DeepAudit-3.0.0\frontend\src\pages\FlowDebugger.tsx`

- [ ] **Step 1: Verify new payloads are preserved through DB-backed event retrieval**
- [ ] **Step 2: Ensure the standalone debugger page renders prompt, tool, and handoff payloads without truncation**
- [ ] **Step 3: Run backend targeted tests**
- [ ] **Step 4: Run frontend build**
- [ ] **Step 5: Rebuild backend and frontend containers if local verification needs the running app**

