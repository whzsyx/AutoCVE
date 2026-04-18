# Agent Direct Audit Design

## Goal

Add a new first-class `Agent直审` module to AuditAI that lets a user open a managed project directory and interact directly with the `finding` agent in a persistent audit session, without going through the existing multi-agent workflow.

The new module must coexist with the current `Agent审计` feature. It should feel like a standalone agent workspace: the user selects a project, opens a directory-backed session, asks for security review or follow-up work in natural language, and continues the same conversation across multiple turns.

## Confirmed Product Decisions

- `Agent直审` is a new module and left-sidebar entry. It does not replace `Agent审计`.
- The old `即时分析` entry is removed and replaced by `Agent直审`.
- `Agent直审` uses the `finding` agent directly and does not require the existing workflow graph or multi-agent orchestration path.
- The primary UI shape is an IDE-style workspace:
  - left: project picker, directory tree, session list
  - center: chat and streaming agent output
  - right: findings and trace panels
- Project management must support three source types:
  - `repository`
  - `zip`
  - `local_directory`
- This delivery must support `local_directory` as a formal project record, not only as a temporary open-directory action.
- Managed local projects live under `AuditAI/projects/<project-name>/...`.
- Temporary arbitrary-directory opening is explicitly deferred.
- `Agent直审` sessions are persistent, follow-up capable conversations.
- The agent is expected to behave as a full-capability agent for the selected project, not a read-only assistant.
- Even with full capability, the runtime should keep minimal safety boundaries around project-root scope and destructive actions.

## Alternatives Considered

### Option 1: Reuse `AuditSession` and `finding_runtime` behind a new direct-audit module

Recommended.

Build a new frontend module and new direct-session APIs, but keep the existing runtime core:

- `finding_runtime`
- `AuditSession`
- runtime tools and skills
- current project workspace preparation logic

Pros:

- Reuses the persistent session, follow-up, tool trace, skill trace, and memory trace foundations that already exist.
- Keeps the direct-audit module aligned with the current finding-agent runtime.
- Lowest implementation risk for a large feature.

Cons:

- Requires lifting `AuditSession` from a task-follow-up role into a first-class frontdoor session model.
- Requires a new API surface for starting direct sessions without an `AgentTask`.

### Option 2: Carve a single-agent mode out of the existing `Agent审计` task flow

Rejected as the primary approach.

Pros:

- Reuses existing finding-agent task setup.

Cons:

- The current task flow is still structurally task-driven and multi-agent-biased.
- It would drag workflow and task lifecycle assumptions into a product surface that should feel like a direct interactive agent.
- Frontend ergonomics would likely remain awkward.

### Option 3: Rebuild a fresh standalone agent shell modeled after `D:\Projects\restored-from-cli-map-v3`

Rejected for this delivery.

Pros:

- The conceptual model is very clean.
- The UX can closely mimic a dedicated standalone agent.

Cons:

- Rebuilds capabilities that AuditAI already has in runtime, trace storage, and project management.
- Creates long-term duplication between two agent stacks.
- Increases maintenance and compatibility costs.

## Architecture Overview

The feature adds a new product module, not a new agent engine.

At a high level:

1. Project management gains a third source type, `local_directory`.
2. Local project directories are managed under `AuditAI/projects/`.
3. The backend resolves any project into a usable workspace root through one unified project-workspace resolver:
   - clone repository
   - extract ZIP
   - point to a managed local directory
4. `Agent直审` starts a persistent `AuditSession` directly from `project_id + user prompt`, without creating a multi-agent workflow task.
5. The backend runs the existing `finding_runtime` against that resolved workspace root.
6. The frontend renders an IDE-style direct-audit workspace around that session.
7. The user continues the same conversation with follow-up prompts such as:
   - "帮我看看这个项目有没有安全漏洞"
   - "再帮我看看还有没有遗漏的"
   - "使用 CVE report writer 技能为你发现的漏洞生成报告"

The design intentionally keeps `Agent审计` and `Agent直审` as separate product surfaces:

- `Agent审计` remains workflow-driven and task-driven.
- `Agent直审` becomes conversation-driven and workspace-driven.

## Product Surface

### Sidebar and Routing

Replace the old visible `即时分析` route with:

- name: `Agent直审`
- path: `/agent-direct-audit`

The existing `Agent审计` routes remain unchanged.

### Agent Direct Audit Page

The new page should live under a dedicated module, for example:

- `frontend/src/pages/AgentDirectAudit/`

Recommended layout:

- left column
  - project selector
  - session list for the selected project
  - directory tree rooted at the active project workspace
- center column
  - session header
  - message timeline
  - streaming assistant output
  - follow-up composer
- right column
  - findings panel
  - tool trace
  - skill trace
  - memory trace

The center and right sections should reuse as much of the current `AuditSession` UI and hooks as practical. The left column is mostly new.

## Session Model

### Direct-Audit Sessions

`Agent直审` should create direct sessions that are project-bound rather than task-bound.

The session model should support:

- one selected project
- one resolved workspace root
- a persistent chat transcript
- follow-up prompts
- tool and skill traces
- session history per project

This delivery should reuse `audit_sessions` rather than inventing a parallel transcript store.

Recommended evolution:

- keep using `AuditSession`
- allow `task_id` to remain empty for direct sessions
- distinguish direct sessions with a session kind field or session metadata

Suggested shape:

- `session_kind = "agent_direct_audit"` for direct sessions
- existing task-created sessions remain distinct

If a dedicated column is too heavy for the first slice, a structured metadata field is acceptable as long as the distinction is queryable and stable.

## Data Model

### Extend `projects`

Expand project source handling from:

- `repository`
- `zip`

to:

- `repository`
- `zip`
- `local_directory`

Suggested additions to the `Project` model:

- `source_type`
  - now explicitly supports `local_directory`
- `local_path`
  - absolute path to the managed project root
- `workspace_mode`
  - reserved for future behavior such as `in_place` vs `copy_on_open`
- `source_metadata_json`
  - optional structured metadata about local import origin and managed state

Notes:

- `local_path` is required for `local_directory` projects.
- `repository_url` remains applicable only to `repository` projects.
- ZIP metadata remains handled by existing ZIP storage services.

### Managed Local Project Root

This delivery standardizes one managed local project root:

- `D:\Projects\AuditAI\projects`

Each managed local project should be represented as:

- `D:\Projects\AuditAI\projects\<project-name>\...`

The design intentionally avoids an extra nested `项目文件` directory unless an implementation constraint forces it.

This keeps:

- relative file references simple
- directory-tree rendering straightforward
- report and finding file paths cleaner

## Project Ingestion

### Project Management: Three Creation Paths

The create-project experience should support:

1. Git repository
2. ZIP upload
3. Local directory import

For this release, local-directory import is not an arbitrary filesystem picker. It is a managed-directory registration flow.

Recommended UX:

1. The backend scans `AuditAI/projects/` for first-level directories.
2. The frontend shows those directories in a new `本地目录` tab in project creation.
3. The user selects one directory and registers it as a formal project.
4. If a directory is already registered, the UI prompts the user to open the existing project instead of duplicating it.

This approach is intentionally narrower than a general directory picker because it:

- reduces permission complexity
- keeps project roots inside one managed namespace
- aligns with the requested product behavior
- keeps future workspace controls simpler

### Deferred Local Open Mode

A temporary "open any directory now" mode is deferred.

The design should leave room for it later, but this delivery must focus on managed project records first.

## Workspace Resolution

The current code already has logic for preparing local project roots for agent execution. This should be formalized into a reusable project-workspace resolver shared by both `Agent审计` and `Agent直审`.

Recommended contract:

- input:
  - project record
  - optional branch
  - optional execution mode
- output:
  - `workspace_root`
  - `workspace_origin`
  - optional cleanup contract

Expected behavior:

- `repository`
  - clone repository into a prepared working directory
- `zip`
  - extract ZIP into a prepared working directory
- `local_directory`
  - use the managed local path directly for direct-audit sessions

Important product rule:

- `Agent直审` should default to in-place operation for `local_directory` projects so the agent feels like it has directly opened the project directory.
- `Agent审计` can continue using task-scoped temporary workspaces where that already matches current behavior.

## Backend API Design

Add a dedicated direct-audit API surface instead of overloading the existing task endpoints.

Suggested capabilities:

- create a direct-audit session
- list direct-audit sessions for a project
- get direct-audit session detail
- continue a direct-audit session with streaming
- get directory tree for a project
- read file content for a project

Suggested endpoint family:

- `POST /agent-direct-audit/sessions`
- `GET /agent-direct-audit/sessions`
- `GET /agent-direct-audit/sessions/{session_id}`
- `POST /agent-direct-audit/sessions/{session_id}/messages/stream`
- `GET /projects/{id}/directory-tree`
- `GET /projects/{id}/file-content`

### Session Creation Contract

`POST /agent-direct-audit/sessions` should accept:

- `project_id`
- `content`
  - first user prompt
- optional `sub_path`
  - future-friendly field for narrowing initial scope

The backend should:

1. validate project ownership
2. resolve the project workspace root
3. initialize a direct `AuditSession`
4. run `finding_runtime`
5. stream the first answer back or return a session identifier for the frontend to attach to

### Follow-Up Contract

Follow-up messages should work exactly like a persistent chat session. The user should not have to restart the audit to ask the next question.

This should reuse the existing streaming follow-up behavior already present for `audit_sessions`, adapted so it works cleanly for direct sessions that were not spawned from `AgentTask`.

## Frontend Module Design

### Reuse Strategy

The new module should compose existing foundations instead of starting from scratch:

- reuse `AuditSession` message and follow-up behavior for the center chat column
- reuse existing trace panels on the right side
- reuse styling patterns from the current workspace-heavy pages
- add a new left column for project context and file navigation

### Left Column

The left column must include:

- project selector
- current project summary
- direct-session history for that project
- directory tree

Recommended interactions:

- switching project updates the directory tree and available session history
- selecting a prior session reopens the same conversation
- selecting a file can preview or at minimum establish context for the current audit

### Center Column

The center column must provide:

- active session header
- streaming chat timeline
- assistant output rendered as Markdown
- follow-up composer
- clear session-state feedback while tools are running

### Right Column

The right column should show session intelligence, not generic page chrome:

- findings
- tool trace
- skill trace
- memory trace

Findings should eventually be clickable back to files. For the first delivery, showing the file path and line reference is sufficient if deep navigation takes longer.

## Runtime Capability Model

The requested product behavior is a full-capability agent for the selected project.

That means the direct-audit runtime should support:

- reading files
- traversing the project tree
- searching code
- calling skills
- generating reports
- executing user-requested actions relevant to the project
- writing files when needed

Even with that full-capability model, the system should keep three minimum runtime boundaries:

1. Default scope is the active project root.
2. Cross-project or cross-root operations require explicit confirmation.
3. High-risk destructive operations require explicit confirmation.

Examples of high-risk actions:

- recursive deletion
- mass overwrite
- broad replacement across many files

This keeps the product aligned with the requested flexibility while still protecting the managed workspace.

### Output Location Guidance

Generated artifacts should prefer one of:

- a managed output area under the project, such as `.auditai/`
- another explicit managed output directory

This avoids mixing generated disclosures and transient artifacts into unrelated source folders by default.

## Interaction With Existing Modules

### `Agent审计`

No behavioral replacement is planned in this design.

`Agent审计` remains:

- workflow-driven
- task-oriented
- multi-agent capable

### `Agent直审`

The new module becomes:

- session-driven
- direct-agent oriented
- project-root oriented

### Shared Foundations

The two modules should share:

- project records
- workspace preparation logic
- finding runtime
- skills runtime
- trace storage where possible

This avoids forking the agent stack.

## Error Handling

- If project registration fails, the user should see whether the failure came from duplicate registration, invalid managed directory, or ownership rules.
- If a direct session fails to initialize, the project record must remain intact.
- If the directory tree fails to load, the user should still be able to access session history and retry the tree.
- If a follow-up runtime step fails, the session transcript should keep the user prompt and append an error state rather than silently dropping the turn.
- If a local-directory path becomes unavailable on disk, the UI should present the project as unavailable and block session start until fixed.

## Testing Strategy

Backend tests:

- project creation for `local_directory`
- managed-directory scanning and duplicate detection
- direct-session creation without `AgentTask`
- direct-session follow-up flow
- workspace resolution for all three project source types
- directory tree retrieval
- file content retrieval
- safety boundary behavior for out-of-root operations

Frontend tests:

- sidebar route replacement from `即时分析` to `Agent直审`
- project management local-directory flow
- direct-audit page loads and starts a session
- session history switching
- directory tree rendering
- follow-up message flow
- right-panel trace rendering

Integration tests:

- register a managed local directory as a project
- start a direct-audit session against that project
- ask a follow-up question in the same session
- confirm that findings and traces remain visible across turns

## Delivery Phases

### Phase 1: Project Source Expansion And Minimum Direct Session Loop

Deliver a complete, minimum viable direct-audit chain:

- add `local_directory` project support
- scan and register managed directories under `AuditAI/projects/`
- replace the sidebar entry
- add the new `Agent直审` route and initial page shell
- create direct sessions from a selected project and first user message
- run `finding_runtime` without the workflow graph
- support follow-up messages in the same session

This phase should end with a usable loop: select project, start session, receive answer, continue conversation.

### Phase 2: IDE Workspace Completion

Complete the main direct-audit workspace:

- add project-scoped session history
- add directory tree
- add richer session header and context controls
- wire findings and trace panels into the right column
- improve reload and session restoration behavior

This phase makes the module feel like a real agent workspace rather than only a chat page.

### Phase 3: Full-Capability Runtime Guardrails

Harden the full-capability agent behavior:

- keep project-root default scope
- add confirmations for cross-root actions
- add confirmations for destructive actions
- define managed output locations
- expose runtime action status clearly in the UI

This phase is about controllability, not reducing capability.

### Phase 4: Deeper Platform Integration

Integrate direct-audit outputs more deeply with the rest of AuditAI:

- improve vulnerability-management handoff
- improve report-generation flows from direct sessions
- add deeper result reuse across modules
- prepare for temporary arbitrary-directory open mode if still desired

## Out Of Scope For This Change

- replacing or removing the existing `Agent审计` module
- introducing arbitrary local-directory browsing outside the managed `AuditAI/projects/` root
- rebuilding a completely separate agent runtime modeled one-to-one after the reference project
- full diff-preview UX for every file write action
- deep automatic linkage from direct-audit findings into vulnerability management in the first delivery

## Open Follow-Up For Planning

The implementation plan should make the following choices explicit:

- whether direct sessions get a dedicated `session_kind` column or use structured metadata in the first slice
- whether directory-tree file preview is read-only in phase one or immediately supports editor-like inline actions
- whether local-directory registration should store only the resolved path or also persist managed-directory scan metadata
- whether direct-session creation returns the first streamed answer immediately or uses a create-then-attach pattern
