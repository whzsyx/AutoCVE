# AuditAI UI Redesign Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Rebuild the DeepAudit frontend shell and core pages into an AuditAI-branded light gray workspace while preserving existing functionality.

**Architecture:** Keep the existing React + Vite application structure, but replace the global visual system, application shell, and key page layouts with a unified AuditAI design layer. Limit behavior changes to presentation, navigation ergonomics, and user-visible brand strings so the existing data flow and APIs remain intact.

**Tech Stack:** React 18, TypeScript, Vite, Tailwind CSS, shadcn/ui, Lucide, Recharts

---

## File Structure

- Modify: `frontend/src/assets/styles/globals.css`
  Responsibility: Replace the current cyberpunk token system with the new AuditAI light theme, typography, cards, surfaces, and utility classes.
- Modify: `frontend/src/app/App.tsx`
  Responsibility: Rebuild the app shell spacing and main content container behavior around the new sidebar.
- Modify: `frontend/src/components/layout/Sidebar.tsx`
  Responsibility: Replace the current cyber sidebar with the new reference-inspired vertical navigation.
- Modify: `frontend/src/components/layout/PageMeta.tsx`
  Responsibility: Change site title and metadata branding to AuditAI.
- Modify: `frontend/index.html`
  Responsibility: Update page title and favicon references.
- Modify: `frontend/src/pages/Dashboard.tsx`
  Responsibility: Rebuild dashboard information hierarchy in the new A+C visual system.
- Modify: `frontend/src/pages/Projects.tsx`
  Responsibility: Rebuild the projects workspace layout and dialog styling.
- Modify: `frontend/src/pages/AgentAudit/index.tsx`
  Responsibility: Reframe the audit workspace into a cleaner three-panel layout.
- Modify: `frontend/src/pages/Login.tsx`
  Responsibility: Update login branding, logo, and typography.
- Modify: `frontend/src/pages/Register.tsx`
  Responsibility: Update registration branding, logo, and typography.
- Modify: `frontend/src/pages/NotFound.tsx`
  Responsibility: Replace DeepAudit brand strings in the error state.
- Modify: `frontend/src/components/database/DatabaseManager.tsx`
  Responsibility: Update exported backup filename branding.
- Modify: `frontend/src/shared/constants/index.ts`
  Responsibility: Replace local storage brand keys that are user-visible or brand-sensitive.
- Modify: `frontend/src/shared/config/env.ts`
  Responsibility: Default app id branding.
- Modify: brand-visible backend files such as `backend/app/core/config.py`, `backend/app/main.py`, `backend/app/services/task_report_service.py`, `backend/app/services/init_agent_assets.py`
  Responsibility: Replace user-visible API welcome text and report branding with AuditAI.
- Add/Modify: `frontend/public/*` logo and favicon assets
  Responsibility: Introduce the dog-head AuditAI icon set.

## Chunk 1: Design System And Shell

### Task 1: Establish verification baseline

**Files:**
- Test: `frontend/package.json`

- [ ] **Step 1: Run a baseline type check before changing code**

Run: `npm run type-check`
Expected: Existing baseline status captured for comparison

- [ ] **Step 2: Run a baseline build before changing code**

Run: `npm run build`
Expected: Existing baseline status captured for comparison

### Task 2: Rewrite the global AuditAI visual system

**Files:**
- Modify: `frontend/src/assets/styles/globals.css`

- [ ] **Step 1: Add or update theme tests/checkpoints by identifying current global class usage**

Run: `rg -n "cyber-|font-mono|gradient-bg|terminal-" frontend/src`
Expected: List of current global style hooks to preserve or replace

- [ ] **Step 2: Replace the root color tokens with the AuditAI palette**

- [ ] **Step 3: Replace global body/background/typography rules with normal sans-serif defaults**

- [ ] **Step 4: Rebuild shared utility classes for cards, section headers, buttons, inputs, tables, and status surfaces**

- [ ] **Step 5: Keep only targeted monospace usage for logs/code-like areas**

### Task 3: Rebuild the application shell and sidebar

**Files:**
- Modify: `frontend/src/app/App.tsx`
- Modify: `frontend/src/components/layout/Sidebar.tsx`
- Modify: `frontend/src/app/routes.tsx`

- [ ] **Step 1: Write a focused shell regression checklist from current behavior**

Expected behaviors:
- Sidebar collapses on desktop
- Mobile menu still works
- Existing route list remains accessible

- [ ] **Step 2: Update `Sidebar.tsx` to the reference-inspired card sidebar**

- [ ] **Step 3: Update `App.tsx` margins, page wrapper, and main panel spacing to match the new shell**

- [ ] **Step 4: Adjust route labeling only if needed for better navigation readability**

## Chunk 2: Brand Replacement

### Task 4: Replace frontend-visible brand strings

**Files:**
- Modify: `frontend/index.html`
- Modify: `frontend/src/components/layout/PageMeta.tsx`
- Modify: `frontend/src/pages/Login.tsx`
- Modify: `frontend/src/pages/Register.tsx`
- Modify: `frontend/src/pages\NotFound.tsx`
- Modify: `frontend/src/components/database/DatabaseManager.tsx`
- Modify: `frontend/src/shared/constants/index.ts`
- Modify: `frontend/src/shared/config/env.ts`

- [ ] **Step 1: Search for user-visible `DeepAudit` strings in frontend code**

Run: `rg -n "DeepAudit|deepaudit" frontend/src frontend/index.html`
Expected: Concrete replacement list

- [ ] **Step 2: Replace page titles, alt text, emails, storage keys, and download names with AuditAI equivalents**

- [ ] **Step 3: Keep only technical identifiers that should remain untouched unless required for runtime compatibility**

### Task 5: Replace logo and favicon assets

**Files:**
- Modify or add: `frontend/public/logo_deepaudit.png`
- Modify or add: `frontend/public/favicon.png`
- Modify or add: `frontend/public/images/logo/*`

- [ ] **Step 1: Prepare the dog-head icon asset without the outer circle**

- [ ] **Step 2: Replace the logo files referenced by the app**

- [ ] **Step 3: Confirm file references used by login/sidebar/meta still resolve**

## Chunk 3: Core Page Redesign

### Task 6: Redesign the dashboard

**Files:**
- Modify: `frontend/src/pages/Dashboard.tsx`

- [ ] **Step 1: Preserve the current data loading logic and derived stats**

- [ ] **Step 2: Rewrite the layout into welcome header, key metrics, recent work, and side insights**

- [ ] **Step 3: Replace cyber cards, badges, and empty states with AuditAI surfaces**

- [ ] **Step 4: Keep charts functional while restyling their containers and labels**

### Task 7: Redesign the projects workspace

**Files:**
- Modify: `frontend/src/pages/Projects.tsx`

- [ ] **Step 1: Preserve create/edit/delete/upload behavior**

- [ ] **Step 2: Rebuild the top toolbar and project listing layout**

- [ ] **Step 3: Replace terminal-themed dialogs with product-style dialogs**

- [ ] **Step 4: Improve form grouping for repository vs ZIP flows without changing backend contracts**

### Task 8: Redesign the agent audit workspace

**Files:**
- Modify: `frontend/src/pages/AgentAudit/index.tsx`
- Modify as needed: `frontend/src/pages/AgentAudit/components/*`

- [ ] **Step 1: Preserve stream logic, task polling, and agent tree interactions**

- [ ] **Step 2: Rework the container layout into a clean three-panel workspace**

- [ ] **Step 3: Reduce visual terminal treatment while preserving operational clarity**

- [ ] **Step 4: Keep logs readable and stats accessible with the new design system**

## Chunk 4: Backend User-Visible Branding

### Task 9: Replace user-visible backend brand strings

**Files:**
- Modify: `backend/app/core/config.py`
- Modify: `backend/app/main.py`
- Modify: `backend/app/services/init_agent_assets.py`
- Modify: `backend/app/services/task_report_service.py`

- [ ] **Step 1: Search backend for user-visible `DeepAudit` strings**

Run: `rg -n "DeepAudit|deepaudit" backend/app`
Expected: Replaceable UI/report/API text inventory

- [ ] **Step 2: Replace report titles, welcome messages, and template descriptions with AuditAI branding**

- [ ] **Step 3: Avoid risky infrastructure renames unless needed for visible output**

## Chunk 5: Verification

### Task 10: Run fresh verification and spot-check brand cleanup

**Files:**
- Test: `frontend/package.json`

- [ ] **Step 1: Run frontend type check**

Run: `npm run type-check`
Expected: exit 0

- [ ] **Step 2: Run frontend build**

Run: `npm run build`
Expected: exit 0

- [ ] **Step 3: Run final brand search for obvious remaining UI strings**

Run: `rg -n "DeepAudit|deepaudit" frontend/src frontend/index.html backend/app`
Expected: Remaining matches are either intentionally technical or require explicit follow-up

- [ ] **Step 4: Summarize residual risks**

Expected risks:
- Non-core pages may still retain legacy layout patterns
- Backend package names and infra defaults may still use historical identifiers where runtime-sensitive

Plan complete and saved to `docs/superpowers/plans/2026-03-27-auditai-ui-redesign.md`. Ready to execute?
