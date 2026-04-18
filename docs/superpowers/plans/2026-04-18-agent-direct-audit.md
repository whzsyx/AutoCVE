# Agent Direct Audit Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the first shippable version of `Agent直审`, including managed local-directory projects, direct finding-runtime sessions, and a new IDE-style workspace entry that coexists with the current `Agent审计` module.

**Architecture:** Extend the existing project model to support `local_directory`, add a dedicated direct-audit backend API that starts `AuditSession` records without `AgentTask`, and build a new frontend workspace that reuses the current audit-session chat and trace foundations. Deliver the feature in two immediate sub-phases: backend/project plumbing first, then the new route and workspace UI.

**Tech Stack:** FastAPI, SQLAlchemy, Alembic, pytest, React, TypeScript, Vite, existing `finding_runtime`, existing `AuditSession` APIs and hooks.

---

## File Structure

### Backend files to create or modify in the first implementation slice

- Modify: `backend/app/models/project.py`
  - Extend project source handling with `local_directory` and local-path fields.
- Create: `backend/alembic/versions/20260418_01_add_local_directory_projects_and_direct_sessions.py`
  - Add project local-directory columns and direct-session metadata storage.
- Modify: `backend/app/api/v1/endpoints/projects.py`
  - Support local-directory project creation, listing of managed local directories, directory tree, and file content APIs.
- Create: `backend/app/api/v1/endpoints/agent_direct_audit.py`
  - Direct session creation, listing, detail, and follow-up streaming APIs.
- Modify: `backend/app/api/v1/api.py`
  - Register the new direct-audit router.
- Create: `backend/app/services/project_workspace.py`
  - Shared project workspace resolver for repository, ZIP, and local-directory projects.
- Modify: `backend/app/api/v1/endpoints/agent_tasks.py`
  - Route existing task execution through the shared project workspace resolver.
- Modify: `backend/app/models/audit_session.py`
  - Add direct-session metadata or explicit `session_kind`.
- Create: `backend/tests/api/test_projects_local_directory_api.py`
  - Cover local-directory creation and managed-directory listing.
- Create: `backend/tests/api/test_agent_direct_audit_api.py`
  - Cover direct-session creation, listing, and direct follow-up flow.
- Modify: `backend/tests/api/test_agent_tasks_runtime_session.py`
  - Verify task execution still works through the shared workspace resolver.

### Frontend files to create or modify in the first implementation slice

- Modify: `frontend/src/app/routes.tsx`
  - Replace the visible `即时分析` route with `Agent直审`.
- Modify: `frontend/src/components/layout/Sidebar.tsx`
  - Replace the navigation icon and label mapping for the removed route.
- Modify: `frontend/src/shared/types/index.ts`
  - Extend project source types and project payload fields.
- Modify: `frontend/src/shared/api/database.ts`
  - Add local-directory project helpers, managed-directory listing, directory tree, and file content APIs.
- Create: `frontend/src/shared/api/agentDirectAudit.ts`
  - Typed direct-session API wrapper.
- Create: `frontend/src/pages/AgentDirectAudit/index.tsx`
  - New IDE-style page shell.
- Create: `frontend/src/pages/AgentDirectAudit/hooks/useAgentDirectAudit.ts`
  - Page state and session orchestration hook.
- Create: `frontend/src/pages/AgentDirectAudit/components/ProjectSessionsPanel.tsx`
  - Left-column project/session panel.
- Create: `frontend/src/pages/AgentDirectAudit/components/DirectoryTreePanel.tsx`
  - Left-column directory tree panel.
- Create: `frontend/src/pages/AgentDirectAudit/components/WorkspaceTracePanel.tsx`
  - Right-column trace wrapper reusing existing trace panels.
- Modify: `frontend/src/pages/Projects.tsx`
  - Add the local-directory project creation tab and open-in-direct-audit entry points.
- Modify: `frontend/src/pages/AuditSession/hooks/useAuditSession.ts`
  - If needed, generalize fetching so direct sessions and task sessions share the same session detail loader.

### Deferred or later-phase files

- Modify: `frontend/src/pages/ProjectDetail.tsx`
  - Future deep linking from direct-audit findings back to project views.
- Modify: `backend/app/services/vulnerability_report_generation.py`
  - Later direct-session integration for managed report workflows.
- Create: `frontend/src/pages/AgentDirectAudit/components/FilePreviewPanel.tsx`
  - Optional richer inline file preview after the first delivery.

## Phase Breakdown

### Phase 1A: Managed Local Projects And Direct Session Backend

Deliver:

- `local_directory` project support
- managed-directory listing under `AuditAI/projects`
- direct-audit backend session APIs
- shared workspace resolver

### Phase 1B: New Route And Direct-Audit Workspace Shell

Deliver:

- replace `即时分析` with `Agent直审`
- local-directory project creation UI
- new direct-audit route
- project selector, session list, directory tree, chat, and trace shell

### Phase 2: Workspace Completion And Guardrails

Deliver:

- stronger file navigation
- improved session restoration
- confirmation flows for cross-root and destructive actions
- managed artifact output conventions

### Phase 3: Deeper Platform Integration

Deliver:

- tighter linkage into findings, vulnerability management, and report workflows
- optional arbitrary-directory temporary open mode if still desired

## Task 1: Extend projects for managed local directories

**Files:**
- Modify: `backend/app/models/project.py`
- Create: `backend/alembic/versions/20260418_01_add_local_directory_projects_and_direct_sessions.py`
- Modify: `backend/app/api/v1/endpoints/projects.py`
- Create: `backend/tests/api/test_projects_local_directory_api.py`
- Modify: `frontend/src/shared/types/index.ts`
- Modify: `frontend/src/shared/api/database.ts`

- [ ] **Step 1: Write the failing backend and frontend contract tests**

Add backend tests that assert:

- creating a project with `source_type="local_directory"` requires a managed local path
- listing managed local directories returns first-level directories from `AuditAI/projects`
- duplicate registration of the same managed local path is rejected

Add a frontend type-level expectation by extending:

```ts
export type ProjectSourceType = 'repository' | 'zip' | 'local_directory';

export interface Project {
  local_path?: string;
  workspace_mode?: string | null;
}
```

- [ ] **Step 2: Run tests to verify they fail**

Run:

```powershell
Set-Location backend
$env:PYTHONPATH='.'
uv run --with pytest --with pytest-asyncio pytest tests/api/test_projects_local_directory_api.py -q
```

Expected: FAIL because `local_directory` support and managed-directory APIs do not exist yet.

- [ ] **Step 3: Implement the minimal schema and API changes**

Add project fields and creation handling:

```python
class Project(Base):
    source_type = Column(String(20), default="repository", nullable=False)
    local_path = Column(String, nullable=True)
    workspace_mode = Column(String(32), nullable=True)
```

Add project creation validation:

```python
if source_type == "local_directory":
    if not project_in.local_path:
        raise HTTPException(status_code=422, detail="local_path is required for local_directory projects")
```

Add managed local-directory listing endpoint logic:

```python
managed_root = Path(settings.MANAGED_PROJECTS_ROOT)
directories = [item for item in managed_root.iterdir() if item.is_dir()]
```

- [ ] **Step 4: Run tests to verify they pass**

Run:

```powershell
Set-Location backend
$env:PYTHONPATH='.'
uv run --with pytest --with pytest-asyncio pytest tests/api/test_projects_local_directory_api.py -q
```

Expected: PASS with local-directory creation and managed-directory listing covered.

- [ ] **Step 5: Commit**

```bash
git add backend/app/models/project.py backend/alembic/versions/20260418_01_add_local_directory_projects_and_direct_sessions.py backend/app/api/v1/endpoints/projects.py backend/tests/api/test_projects_local_directory_api.py frontend/src/shared/types/index.ts frontend/src/shared/api/database.ts
git commit -m "feat: add managed local directory projects"
```

## Task 2: Add a shared project workspace resolver

**Files:**
- Create: `backend/app/services/project_workspace.py`
- Modify: `backend/app/api/v1/endpoints/agent_tasks.py`
- Modify: `backend/app/api/v1/endpoints/projects.py`
- Modify: `backend/tests/api/test_agent_tasks_runtime_session.py`
- Create: `backend/tests/api/test_agent_direct_audit_api.py`

- [ ] **Step 1: Write the failing tests**

Cover:

- `repository` projects still resolve to a prepared working copy
- `zip` projects still resolve to an extracted working copy
- `local_directory` projects resolve directly to `local_path`

Use a focused resolver contract:

```python
workspace = await resolve_project_workspace(project=project, purpose="direct_audit")
assert workspace.root_path == expected_path
assert workspace.origin == "local_directory"
```

- [ ] **Step 2: Run tests to verify they fail**

Run:

```powershell
Set-Location backend
$env:PYTHONPATH='.'
uv run --with pytest --with pytest-asyncio pytest tests/api/test_agent_tasks_runtime_session.py tests/api/test_agent_direct_audit_api.py -q
```

Expected: FAIL because the shared resolver and direct-audit tests do not exist yet.

- [ ] **Step 3: Implement the minimal shared resolver**

Create a shared resolver shape:

```python
@dataclass
class ProjectWorkspace:
    root_path: str
    origin: str
    cleanup_required: bool = False
```

Add resolver branches:

```python
if project.source_type == "local_directory":
    return ProjectWorkspace(root_path=project.local_path, origin="local_directory", cleanup_required=False)
```

Then update `agent_tasks.py` to call the shared resolver instead of owning all source-type branching inline.

- [ ] **Step 4: Run tests to verify they pass**

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/project_workspace.py backend/app/api/v1/endpoints/agent_tasks.py backend/app/api/v1/endpoints/projects.py backend/tests/api/test_agent_tasks_runtime_session.py backend/tests/api/test_agent_direct_audit_api.py
git commit -m "refactor: share project workspace resolution"
```

## Task 3: Add direct-audit backend session APIs

**Files:**
- Create: `backend/app/api/v1/endpoints/agent_direct_audit.py`
- Modify: `backend/app/api/v1/api.py`
- Modify: `backend/app/models/audit_session.py`
- Create: `backend/tests/api/test_agent_direct_audit_api.py`

- [ ] **Step 1: Write the failing API tests**

Cover:

- create direct session from `project_id + content`
- list direct sessions for a project
- follow-up message appends to the same session
- direct sessions are distinguishable from task-bound sessions

Test payload shape:

```python
response = await client.post(
    "/api/v1/agent-direct-audit/sessions",
    json={"project_id": "project-1", "content": "audit this project"},
)
assert response.status_code == 200
assert response.json()["project_id"] == "project-1"
assert response.json()["task_id"] is None
```

- [ ] **Step 2: Run tests to verify they fail**

- [ ] **Step 3: Implement the minimum direct-session path**

Add a distinguishable direct-session marker:

```python
runtime_state_json = {
    **(session.runtime_state_json or {}),
    "session_kind": "agent_direct_audit",
    "workspace_root": workspace_root,
}
```

Add creation flow:

```python
session = AuditSession(
    project_id=project.id,
    task_id=None,
    runtime_stack="runtime",
    state="running",
    runtime_state_json={"session_kind": "agent_direct_audit"},
)
```

Start runtime with the selected project workspace and first user message.

- [ ] **Step 4: Run tests to verify they pass**

- [ ] **Step 5: Commit**

```bash
git add backend/app/api/v1/endpoints/agent_direct_audit.py backend/app/api/v1/api.py backend/app/models/audit_session.py backend/tests/api/test_agent_direct_audit_api.py
git commit -m "feat: add direct audit session api"
```

## Task 4: Replace the route and add project-management local-directory UI

**Files:**
- Modify: `frontend/src/app/routes.tsx`
- Modify: `frontend/src/components/layout/Sidebar.tsx`
- Modify: `frontend/src/pages/Projects.tsx`
- Modify: `frontend/src/shared/types/index.ts`
- Modify: `frontend/src/shared/api/database.ts`

- [ ] **Step 1: Write the failing UI-facing tests or contract assertions**

At minimum, verify route configuration and labels by asserting:

```ts
expect(routes.some((route) => route.path === '/agent-direct-audit' && route.name === 'Agent直审')).toBe(true);
expect(routes.some((route) => route.path === '/instant-analysis')).toBe(false);
```

If no existing frontend test harness is ready, document this as a route-contract test to be implemented alongside the route edits and verify via build.

- [ ] **Step 2: Run type-check/build to verify the current app does not yet support the new route**

Run:

```powershell
Set-Location frontend
npm run build
```

Expected: the feature is still absent, so route and UI support are not yet present.

- [ ] **Step 3: Implement route and project-creation changes**

Replace the visible route:

```tsx
{ name: 'Agent直审', path: '/agent-direct-audit', element: <AgentDirectAudit />, visible: true }
```

Add a third project creation tab:

```tsx
<TabsTrigger value="local-directory">
  <Folder className="w-4 h-4 mr-2" />
  本地目录
</TabsTrigger>
```

Populate it from the new managed-directory API and submit `source_type: "local_directory"` with `local_path`.

- [ ] **Step 4: Run build to verify it passes**

- [ ] **Step 5: Commit**

```bash
git add frontend/src/app/routes.tsx frontend/src/components/layout/Sidebar.tsx frontend/src/pages/Projects.tsx frontend/src/shared/types/index.ts frontend/src/shared/api/database.ts
git commit -m "feat: add direct audit route and local project ui"
```

## Task 5: Build the initial Agent Direct Audit workspace

**Files:**
- Create: `frontend/src/shared/api/agentDirectAudit.ts`
- Create: `frontend/src/pages/AgentDirectAudit/index.tsx`
- Create: `frontend/src/pages/AgentDirectAudit/hooks/useAgentDirectAudit.ts`
- Create: `frontend/src/pages/AgentDirectAudit/components/ProjectSessionsPanel.tsx`
- Create: `frontend/src/pages/AgentDirectAudit/components/DirectoryTreePanel.tsx`
- Create: `frontend/src/pages/AgentDirectAudit/components/WorkspaceTracePanel.tsx`
- Modify: `frontend/src/pages/AuditSession/hooks/useAuditSession.ts`

- [ ] **Step 1: Write the failing page-level behavior checks**

Cover:

- page loads project list
- selecting a project loads direct-session history and directory tree
- creating a session sends the first prompt
- follow-up continues the same session

Minimum API contract:

```ts
const session = await createAgentDirectAuditSession({
  project_id: selectedProjectId,
  content: initialPrompt,
});
setActiveSessionId(session.id);
```

- [ ] **Step 2: Run frontend build to verify the page does not yet exist**

- [ ] **Step 3: Implement the first usable workspace shell**

Use a three-column layout:

```tsx
<div className="grid gap-6 xl:grid-cols-[320px_minmax(0,1.5fr)_420px]">
  <ProjectSessionsPanel />
  <AuditTimeline ... />
  <WorkspaceTracePanel ... />
</div>
```

Reuse the existing `AuditSession` timeline and follow-up composer where practical, but orchestrate them from the new direct-audit page state.

- [ ] **Step 4: Run build to verify it passes**

Run:

```powershell
Set-Location frontend
npm run build
```

Expected: PASS with the new `Agent直审` route and page shell.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/shared/api/agentDirectAudit.ts frontend/src/pages/AgentDirectAudit frontend/src/pages/AuditSession/hooks/useAuditSession.ts
git commit -m "feat: add agent direct audit workspace"
```

## Task 6: Verify the full Phase 1 flow

**Files:**
- Test: `backend/tests/api/test_projects_local_directory_api.py`
- Test: `backend/tests/api/test_agent_direct_audit_api.py`
- Test: `backend/tests/api/test_agent_tasks_runtime_session.py`
- Modify as needed based on failures from the above tasks

- [ ] **Step 1: Run the backend verification suite**

```powershell
Set-Location backend
$env:PYTHONPATH='.'
uv run --with pytest --with pytest-asyncio pytest tests/api/test_projects_local_directory_api.py tests/api/test_agent_direct_audit_api.py tests/api/test_agent_tasks_runtime_session.py -q
```

Expected: PASS with local-directory projects, shared resolver behavior, and direct-session APIs covered.

- [ ] **Step 2: Run the frontend verification build**

```powershell
Set-Location frontend
npm run build
```

Expected: PASS with the new route and workspace shell.

- [ ] **Step 3: Commit any final Phase 1 fixes**

```bash
git add backend/tests/api/test_projects_local_directory_api.py backend/tests/api/test_agent_direct_audit_api.py backend/tests/api/test_agent_tasks_runtime_session.py frontend/src/app/routes.tsx frontend/src/components/layout/Sidebar.tsx frontend/src/pages/Projects.tsx frontend/src/pages/AgentDirectAudit
git commit -m "test: verify phase 1 direct audit flow"
```

## Self-Review

### Spec coverage

This plan maps the approved design into the following concrete implementation slices:

- managed `local_directory` project records: Task 1
- shared workspace resolver for all project types: Task 2
- direct-audit session API without `AgentTask`: Task 3
- replace `即时分析` with `Agent直审`: Task 4
- IDE-style direct-audit workspace shell: Task 5
- Phase 1 verification loop: Task 6

No approved Phase 1 requirement is intentionally skipped.

### Placeholder scan

Checked:

- no `TODO`
- no `TBD`
- no "similar to task N"
- no undefined file targets

### Type consistency

The plan consistently uses:

- `local_directory` as the new `ProjectSourceType`
- `local_path` as the managed local project root field
- `session_kind = "agent_direct_audit"` as the preferred session discriminator

## Execution Handoff

Plan complete and saved to `docs/superpowers/plans/2026-04-18-agent-direct-audit.md`. The user explicitly asked to start implementation by phase, so execution should proceed with **Inline Execution** starting at **Task 1 / Phase 1A**.
