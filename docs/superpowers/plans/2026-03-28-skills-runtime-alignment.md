# DeepAudit Skills Runtime Alignment Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace DeepAudit's current mixed Skills mechanism with a unified OpenClaw-style runtime while keeping current external APIs and tool names compatible.

**Architecture:** Build a new `skills_runtime` package that owns discovery, migration, filtering, prompt generation, and safe access. Keep existing service and tool entry points as compatibility wrappers over the runtime, then progressively move agent prompt logic and binding enforcement onto that runtime.

**Tech Stack:** Python, pytest, filesystem-backed skill storage, existing FastAPI and agent service stack.

---

## Chunk 1: Runtime Foundation

### Task 1: Create normalized runtime package skeleton

**Files:**
- Create: `backend/app/services/skills_runtime/__init__.py`
- Create: `backend/app/services/skills_runtime/models.py`
- Create: `backend/app/services/skills_runtime/discovery.py`
- Create: `backend/app/services/skills_runtime/filters.py`
- Create: `backend/app/services/skills_runtime/prompt.py`
- Create: `backend/app/services/skills_runtime/access.py`
- Create: `backend/app/services/skills_runtime/catalog.py`
- Create: `backend/app/services/skills_runtime/migration.py`
- Test: `backend/tests/skills_runtime/test_runtime_models.py`

- [ ] Step 1: Write failing tests for normalized runtime models and prompt state containers.
- [ ] Step 2: Run `pytest backend/tests/skills_runtime/test_runtime_models.py -q` and verify failure.
- [ ] Step 3: Add minimal runtime model helpers for skill entries, bindings, snapshots, and prompt state.
- [ ] Step 4: Run the same test command and verify it passes.

### Task 2: Implement canonical discovery and frontmatter normalization

**Files:**
- Modify: `backend/app/services/skills_runtime/discovery.py`
- Modify: `backend/app/services/skills_runtime/models.py`
- Test: `backend/tests/skills_runtime/test_discovery.py`

- [ ] Step 1: Write discovery tests for canonical `skill_library/<slug>/SKILL.md` loading, frontmatter parsing, malformed skill handling, and deterministic ordering.
- [ ] Step 2: Run `pytest backend/tests/skills_runtime/test_discovery.py -q` and verify failure.
- [ ] Step 3: Implement discovery with canonical source roots and normalized metadata extraction.
- [ ] Step 4: Run the discovery test command and verify it passes.

### Task 3: Implement path-safe access helpers

**Files:**
- Modify: `backend/app/services/skills_runtime/access.py`
- Test: `backend/tests/skills_runtime/test_access.py`

- [ ] Step 1: Write tests for body read, directory listing, file read, `..` rejection, non-file rejection, and symlink escape rejection.
- [ ] Step 2: Run `pytest backend/tests/skills_runtime/test_access.py -q` and verify failure.
- [ ] Step 3: Implement safe realpath-bounded access helpers.
- [ ] Step 4: Run the access test command and verify it passes.

## Chunk 2: Migration and Filtering

### Task 4: Implement legacy binding and metadata migration

**Files:**
- Modify: `backend/app/services/skills_runtime/catalog.py`
- Modify: `backend/app/services/skills_runtime/migration.py`
- Test: `backend/tests/skills_runtime/test_migration.py`

- [ ] Step 1: Write tests covering source metadata loading, legacy agent `bindings.json`, mirror `binding.json`, and migrated binding synthesis.
- [ ] Step 2: Run `pytest backend/tests/skills_runtime/test_migration.py -q` and verify failure.
- [ ] Step 3: Implement migration readers and normalized catalog composition.
- [ ] Step 4: Run the migration test command and verify it passes.

### Task 5: Implement binding-aware filtering and matched-skill selection

**Files:**
- Modify: `backend/app/services/skills_runtime/filters.py`
- Modify: `backend/app/services/skills_runtime/catalog.py`
- Test: `backend/tests/skills_runtime/test_filters.py`

- [ ] Step 1: Write tests for enabled state, `always_include`, keyword matching, tag fallback, and unbound-skill exclusion.
- [ ] Step 2: Run `pytest backend/tests/skills_runtime/test_filters.py -q` and verify failure.
- [ ] Step 3: Implement normalized filtering and matched-skill selection.
- [ ] Step 4: Run the filters test command and verify it passes.

### Task 6: Implement OpenClaw-style prompt state generation

**Files:**
- Modify: `backend/app/services/skills_runtime/prompt.py`
- Test: `backend/tests/skills_runtime/test_prompt.py`

- [ ] Step 1: Write tests for available-skill prompt generation, compact location rendering, and deterministic prompt ordering.
- [ ] Step 2: Run `pytest backend/tests/skills_runtime/test_prompt.py -q` and verify failure.
- [ ] Step 3: Implement prompt state helpers that produce prompt-visible catalogs and runtime guidance inputs.
- [ ] Step 4: Run the prompt test command and verify it passes.

## Chunk 3: Compatibility Layer Replacement

### Task 7: Convert `SkillService` into a runtime adapter

**Files:**
- Modify: `backend/app/services/agent/skill_service.py`
- Test: `backend/tests/skills_runtime/test_compat_skill_service.py`
- Test: `backend/tests/agent/test_agent_contracts.py`

- [ ] Step 1: Add failing tests for runtime-backed `resolve_agent_skills`, `get_skill_body`, `list_skill_resources`, and `get_skill_resource`.
- [ ] Step 2: Run `pytest backend/tests/skills_runtime/test_compat_skill_service.py -q` and verify failure.
- [ ] Step 3: Refactor `SkillService` to call the new runtime and enforce binding-aware access.
- [ ] Step 4: Run the compatibility test command and verify it passes.

### Task 8: Convert `SkillFileService` into storage and migration compatibility only

**Files:**
- Modify: `backend/app/services/skill_file_service.py`
- Test: `backend/tests/skills_runtime/test_compat_skill_file_service.py`

- [ ] Step 1: Write failing tests for compatibility reads and writes that preserve source skill layout without recreating mirror directories.
- [ ] Step 2: Run `pytest backend/tests/skills_runtime/test_compat_skill_file_service.py -q` and verify failure.
- [ ] Step 3: Refactor `SkillFileService` to delegate discovery, reads, and binding refresh behavior to runtime-compatible storage rules.
- [ ] Step 4: Run the compatibility file service test command and verify it passes.

### Task 9: Retain external tool APIs but route through runtime access

**Files:**
- Modify: `backend/app/services/agent/tools/skill_tool.py`
- Test: `backend/tests/agent/test_agent_contracts.py`

- [ ] Step 1: Add or update failing tests for default skill resolution, auto-list fallback, batch read, and bound-skill enforcement.
- [ ] Step 2: Run `pytest backend/tests/agent/test_agent_contracts.py -q` and verify the updated skill-tool assertions fail for the right reasons.
- [ ] Step 3: Refactor skill tools to use runtime-backed reads and listings without changing tool names or argument shape.
- [ ] Step 4: Run the agent contracts test command and verify it passes.

## Chunk 4: Agent Integration and Cleanup

### Task 10: Move Finding prompt integration onto runtime prompt state

**Files:**
- Modify: `backend/app/services/agent/agents/finding_skill_overlay.py`
- Modify: `backend/app/services/agent/agents/finding.py`
- Test: `backend/tests/agent/test_agent_contracts.py`

- [ ] Step 1: Write failing tests for runtime-derived fixed reads, route-specific reads, and prompt wording that still references the compatible external tools.
- [ ] Step 2: Run `pytest backend/tests/agent/test_agent_contracts.py -q` and verify failure.
- [ ] Step 3: Refactor Finding prompt generation to consume runtime prompt state and routing outputs rather than ad hoc file service assumptions.
- [ ] Step 4: Run the agent contracts test command and verify it passes.

### Task 11: Stop mirror directories from being part of the active load path

**Files:**
- Modify: `backend/app/services/init_agent_assets.py`
- Modify: `backend/app/services/skill_file_service.py`
- Test: `backend/tests/skills_runtime/test_migration.py`
- Test: `backend/tests/skills_runtime/test_compat_skill_file_service.py`

- [ ] Step 1: Write failing tests confirming migration reads legacy mirrors but new writes no longer recreate active mirror trees.
- [ ] Step 2: Run `pytest backend/tests/skills_runtime/test_migration.py backend/tests/skills_runtime/test_compat_skill_file_service.py -q` and verify failure.
- [ ] Step 3: Refactor initialization and binding refresh flows so only canonical source skills and binding declarations are maintained.
- [ ] Step 4: Run the same test command and verify it passes.

### Task 12: Full regression verification

**Files:**
- Modify: `backend/tests/agent/test_agent_contracts.py`
- Modify: `backend/tests/agent/test_tools.py`
- Modify: `backend/tests/skills_runtime/*`

- [ ] Step 1: Run `pytest backend/tests/skills_runtime backend/tests/agent/test_agent_contracts.py backend/tests/agent/test_tools.py -q`.
- [ ] Step 2: Fix any failing compatibility or regression behavior with the smallest safe code changes.
- [ ] Step 3: Re-run the full regression suite and verify it passes.
- [ ] Step 4: Run `pytest backend/tests/agent -q` and record any remaining unrelated failures.
