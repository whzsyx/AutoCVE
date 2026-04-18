# Finding Runtime Tool System Alignment Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Align AuditAI's runtime tool system with the restored tool platform architecture, starting with restored-style tool definitions and builder defaults, while explicitly excluding MCP-specific migration for now.

**Architecture:** AuditAI should evolve from a thin runtime execution DTO into a restored-inspired tool platform with three layers: tool definition/build defaults, tool-pool assembly and defer/search behavior, and execution/orchestration semantics. This plan intentionally migrates the foundation first so later shell, lifecycle, and streaming work reuse one consistent runtime tool contract instead of introducing parallel execution-specific metadata.

**Tech Stack:** Python, Pydantic, SQLAlchemy session store, AuditAI finding runtime, restored TypeScript sources (`Tool.ts`, `tools.ts`, `toolOrchestration.ts`, `toolExecution.ts`, `StreamingToolExecutor.ts`).

---

## Scope And References

### Restored source of truth
- `D:\文件\pythonProject\AICVE\AutoCVE\Projects\package\restored-from-cli-map-v3\src\Tool.ts`
- `D:\文件\pythonProject\AICVE\AutoCVE\Projects\package\restored-from-cli-map-v3\src\tools.ts`
- `D:\文件\pythonProject\AICVE\AutoCVE\Projects\package\restored-from-cli-map-v3\src\services\tools\toolOrchestration.ts`
- `D:\文件\pythonProject\AICVE\AutoCVE\Projects\package\restored-from-cli-map-v3\src\services\tools\toolExecution.ts`
- `D:\文件\pythonProject\AICVE\AutoCVE\Projects\package\restored-from-cli-map-v3\src\services\tools\StreamingToolExecutor.ts`

### Current AuditAI runtime files
- `D:\文件\pythonProject\AICVE\AutoCVE\AuditAI-1.0.0\.worktrees\runtime-alignment-gap-analysis\backend\app\services\runtime_core\tool_runtime.py`
- `D:\文件\pythonProject\AICVE\AutoCVE\AuditAI-1.0.0\.worktrees\runtime-alignment-gap-analysis\backend\app\services\runtime_core\runtime_tool_registry.py`
- `D:\文件\pythonProject\AICVE\AutoCVE\AuditAI-1.0.0\.worktrees\runtime-alignment-gap-analysis\backend\app\services\finding_runtime\query_loop.py`
- `D:\文件\pythonProject\AICVE\AutoCVE\AuditAI-1.0.0\.worktrees\runtime-alignment-gap-analysis\backend\app\services\finding_runtime\tooling.py`
- `D:\文件\pythonProject\AICVE\AutoCVE\AuditAI-1.0.0\.worktrees\runtime-alignment-gap-analysis\backend\tests\finding_runtime\test_tool_orchestrator.py`

### Explicit non-goals
- MCP tool migration is out of scope for this plan.
- UI-only renderer parity is out of scope unless needed to preserve runtime transcript semantics.
- Recon, Scan, Triage, and Verification runtime adoption is out of scope here; this plan only changes shared runtime tooling plus Finding runtime consumers.

## Current Gap Summary

AuditAI currently has a thin `RuntimeTool` abstraction (`name`, `description`, `input_model`, validation, permission, execution, basic concurrency) and a batch-based orchestrator. Restored has a platform-level `Tool` contract with defaulted builder semantics, aliasing, richer metadata, defer/loading controls, richer permission semantics, shell tools, lifecycle progress, and a streaming executor.

The migration should proceed in this order:
1. Tool definition and builder defaults
2. Shell tools for audit workflows
3. Deferred tools and ToolSearch
4. Richer single-tool lifecycle
5. Streaming execution parity

This ordering mirrors restored's layering and avoids coupling future runtime behaviors to today's thin DTO.

## File Structure

### Phase 1 target files
- Create: `backend/tests/finding_runtime/test_runtime_tool_definition.py`
- Modify: `backend/app/services/runtime_core/tool_runtime.py`
- Modify: `backend/app/services/runtime_core/runtime_tool_registry.py`
- Modify: `backend/app/services/finding_runtime/tooling.py`
- Modify: `backend/tests/finding_runtime/test_tool_orchestrator.py`

### Likely later-phase files
- Create: `backend/app/services/runtime_core/shell_runtime_tools.py`
- Create: `backend/app/services/runtime_core/tool_search_runtime.py`
- Create: `backend/app/services/runtime_core/streaming_tool_executor.py`
- Modify: `backend/app/services/finding_runtime/query_loop.py`
- Modify: `backend/tests/finding_runtime/test_query_loop.py`

## Phase Breakdown

## Chunk 1: Phase 1 - Tool Definition / Builder Foundation

### Task 1: Add restored-style builder defaults to RuntimeTool

**Files:**
- Modify: `backend/app/services/runtime_core/tool_runtime.py`
- Modify: `backend/app/services/finding_runtime/tooling.py`
- Test: `backend/tests/finding_runtime/test_runtime_tool_definition.py`

- [ ] **Step 1: Write the failing tests for defaulted metadata and builder behavior**

Cover restored-inspired defaults:
- `is_enabled -> True`
- `is_concurrency_safe -> False`
- `is_read_only -> False`
- `is_destructive -> False`
- `interrupt_behavior -> "block"`
- `requires_user_interaction -> False`
- `should_defer -> False`
- `always_load -> False`
- `search_hint -> None`
- `aliases -> []`
- `check_permission -> allow`
- `user_facing_name -> name`

Also verify explicit overrides survive builder construction.

- [ ] **Step 2: Run the focused tests and verify they fail for the right reason**

Run: `uv run --with pytest --with pytest-asyncio pytest tests/finding_runtime/test_runtime_tool_definition.py -q`

Expected: FAIL because the builder or richer metadata contract does not exist yet.

- [ ] **Step 3: Implement a restored-inspired `build_runtime_tool(...)` path**

Implementation goals:
- Preserve backward compatibility for existing `RuntimeTool` subclasses.
- Allow both subclassed tools and builder-constructed tools.
- Add richer metadata fields to `RuntimeTool`.
- Keep `describe()` stable for current consumers while extending it with future-safe metadata.
- Keep MCP-related fields out for now.

- [ ] **Step 4: Re-run the focused tests and make them pass**

Run: `uv run --with pytest --with pytest-asyncio pytest tests/finding_runtime/test_runtime_tool_definition.py -q`

Expected: PASS.

### Task 2: Make registry behavior aware of aliases and enable flags

**Files:**
- Modify: `backend/app/services/runtime_core/tool_runtime.py`
- Modify: `backend/app/services/runtime_core/runtime_tool_registry.py`
- Test: `backend/tests/finding_runtime/test_runtime_tool_definition.py`
- Test: `backend/tests/finding_runtime/test_tool_orchestrator.py`

- [ ] **Step 1: Write the failing tests for alias lookup and enabled-tool description output**

Cover:
- registry lookup by alias
- alias collisions rejected or deterministically blocked
- disabled tools omitted from `describe_tools()` and initial tool pool assembly
- describe output includes future-needed metadata such as `read_only`, `destructive`, `interrupt_behavior`, `requires_user_interaction`, `should_defer`, `always_load`, and `search_hint`

- [ ] **Step 2: Run the focused tests and verify they fail**

Run: `uv run --with pytest --with pytest-asyncio pytest tests/finding_runtime/test_runtime_tool_definition.py tests/finding_runtime/test_tool_orchestrator.py -q`

Expected: FAIL because registry and describe output still only support the thin contract.

- [ ] **Step 3: Implement alias-aware registry and richer tool descriptions**

Implementation goals:
- keep existing tool names stable
- allow alias lookup without duplicating registered tools in output
- filter disabled tools from description output and from later pool assembly call sites
- preserve current orchestrator execution behavior

- [ ] **Step 4: Re-run the focused tests and make them pass**

Run: `uv run --with pytest --with pytest-asyncio pytest tests/finding_runtime/test_runtime_tool_definition.py tests/finding_runtime/test_tool_orchestrator.py -q`

Expected: PASS.

## Chunk 2: Phase 2 - Shell Tooling For Audit Flows

### Goals
- Add restored-inspired shell capability to AuditAI runtime without waiting for streaming executor parity.
- Implement `Bash` plus Windows-appropriate `PowerShell` runtime tools.
- Add safe default metadata for read-only vs destructive shell usage and interrupt behavior.

### Non-goals
- No MCP shell bridging.
- No streaming shell execution yet.

## Chunk 3: Phase 3 - Deferred Tools / ToolSearch

### Goals
- Introduce `should_defer`, `always_load`, and a restored-inspired `ToolSearch` runtime path.
- Keep initial tool prompts smaller while allowing future expansion.

### Non-goals
- No MCP tool search.
- No full restored feature-flag matrix yet.

## Chunk 4: Phase 4 - Richer Tool Lifecycle

### Goals
- Move single-tool execution toward restored `toolExecution.ts` semantics.
- Add alias fallback, richer validation/permission outcomes, progress events, and better error taxonomy.
- Reserve a `context_modifier` hook for later orchestration parity.

### Non-goals
- No full streaming execution yet.

## Chunk 5: Phase 5 - Streaming Tool Executor Parity

### Goals
- Add restored-inspired streaming execution states (`queued`, `executing`, `completed`, `yielded`).
- Execute tool calls while tool-use blocks arrive instead of only after full-turn completion.
- Support ordered draining and sibling cancellation semantics for shell-heavy batches.

### Non-goals
- No MCP executor integration.

## Acceptance Criteria
- Phase 1 leaves existing Finding runtime behavior green while expanding the tool contract.
- Tool metadata and builder defaults are explicit and test-covered.
- Existing concrete tools (`Read`, `Glob`, `Grep`, `Skill`, `TodoWrite`, `AskUser`, `EnterPlanMode`, `ExitPlanMode`) continue to work unchanged or with only metadata-level edits.
- Future phases can build shell, defer/search, lifecycle, and streaming support on one shared runtime tool contract.

## Verification Commands
- `cd backend`
- `uv run --with pytest --with pytest-asyncio pytest tests/finding_runtime/test_runtime_tool_definition.py tests/finding_runtime/test_tool_orchestrator.py -q`
- `uv run --with pytest --with pytest-asyncio pytest tests/finding_runtime -q`

Plan complete and saved to `docs/superpowers/plans/2026-04-18-finding-runtime-tool-system-alignment-plan.md`. Ready to execute.
