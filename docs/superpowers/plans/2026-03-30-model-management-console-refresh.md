# Model Management Console Refresh Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Refresh the DeepAudit model management page into a cleaner shallow gray + green configuration console, replace long model button lists with searchable model picking plus manual input, remove the Always Thinking controls, and rename Runtime Env to manual Env JSON configuration.

**Architecture:** Keep the change scoped to the existing `SystemConfig` page so the backend contract stays stable. Rebuild the page around a small reusable model picker inside the same component file, normalize hidden `alwaysThinkingEnabled` values to `false`, and keep runtime Env JSON support because the backend actively consumes it for provider/model/base URL/API key fallback resolution.

**Tech Stack:** React, TypeScript, Tailwind CSS, existing Radix UI wrappers (`Select`, `Popover`, `Command`, `Dialog`)

---

## Chunk 1: Console Layout And Model Input Refresh

### Task 1: Document the write scope

**Files:**
- Modify: `frontend/src/components/system/SystemConfig.tsx`
- Verify: `frontend/package.json`

- [ ] **Step 1: Confirm the page owns both global and agent model cards**

Run: `rg -n "Always Thinking|Runtime Env|Model|agentConfigs" frontend/src/components/system/SystemConfig.tsx`
Expected: matches in the single page component that owns the global card and each agent card

- [ ] **Step 2: Replace the loose warm-tone layout with a unified console surface**

Implement a shallower gray + green visual language across the hero, global settings panel, principles panel, and agent cards. Keep the test dialog functional while aligning it with the updated palette.

- [ ] **Step 3: Add a searchable recommended-model picker plus manual model input**

Build a reusable model chooser inside `frontend/src/components/system/SystemConfig.tsx` using the existing `Popover` + `Command` components for searchable recommended models and an `Input` for direct model ID entry.

- [ ] **Step 4: Remove visible Always Thinking controls and zero the hidden values**

Do not render the controls in the UI. When loading or saving configuration, force `alwaysThinkingEnabled` to `false` for the global config and each agent config so the hidden option does not keep affecting runtime behavior.

- [ ] **Step 5: Rename Runtime Env to manual Env configuration**

Update the global and agent labels/help text to `手动配置 Env（JSON）`, and explain that the field is used to override runtime environment values such as API key, base URL, model, and timeout.

- [ ] **Step 6: Keep the agent cards behaviorally consistent**

Apply the same provider/model/runtime Env treatment to every agent card, preserve inheritance behavior, and continue showing the effective provider/model summary plus the test-chat entry point.

- [ ] **Step 7: Verify the page compiles cleanly**

Run: `npm run type-check`
Expected: exit code 0 with no TypeScript errors

- [ ] **Step 8: Run a production build check**

Run: `npm run build`
Expected: exit code 0 and Vite build output
