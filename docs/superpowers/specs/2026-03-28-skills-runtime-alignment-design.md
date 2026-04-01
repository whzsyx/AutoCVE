# DeepAudit Skills Runtime Alignment Design

**Date**: 2026-03-28

**Goal**: Rebuild DeepAudit's Skills mechanism so the internal architecture, loading flow, runtime prompt injection, and safety boundaries align as closely as practical with OpenClaw, while keeping existing external APIs and tool names working.

## Background

DeepAudit's current Skills mechanism mixes filesystem storage, agent bindings, mirrored per-agent skill copies, tool-facing access APIs, and Finding-specific overlay logic into one path. That works, but it also creates persistent problems:

- path access is not safely bounded to a skill root
- binding metadata is advisory rather than a true access boundary
- mirrored agent skill trees duplicate data and drift from source skills
- prompt-time routing is partly hard-coded and partly metadata-driven
- the skill runtime is not reusable across agents

OpenClaw uses a cleaner model:

- discover skills from configured roots
- parse frontmatter into structured metadata
- filter by runtime eligibility and configuration
- generate a compact prompt-visible skill catalog
- let runtime and tools read directly from selected skill roots

This refactor adopts that model inside DeepAudit and keeps compatibility at the edges.

## Design Goals

1. Keep current external APIs stable.
2. Automatically migrate and continue to read existing `skill_library` data.
3. Remove mirrored per-agent skill trees from the primary runtime path.
4. Introduce a single runtime responsible for discovery, filtering, prompt generation, and safe resource access.
5. Make binding state a real access boundary.
6. Move Finding-specific routing from ad hoc overlay behavior into runtime-produced guidance wherever possible.

## Target Architecture

Add a new runtime package under `backend/app/services/skills_runtime/`:

- `models.py`: normalized runtime types such as `SkillEntry`, `SkillBinding`, `SkillPromptState`, and `SkillSnapshot`
- `discovery.py`: skill root discovery, `SKILL.md` validation, frontmatter parsing, metadata normalization
- `catalog.py`: compatibility data loading from legacy metadata and bindings
- `filters.py`: per-agent binding filtering, enabled state, keyword and tag matching
- `prompt.py`: OpenClaw-style available-skill prompt generation and compact guidance
- `access.py`: safe body/resource access with canonical path containment checks
- `migration.py`: legacy binding and mirror migration bookkeeping

## Compatibility Layer

Keep these public surfaces intact:

- `SkillFileService`
- `SkillService`
- `load_skill_body`
- `skill_resource_lookup`
- `/api/v1/skills`
- `/api/v1/config` skill briefing outputs

Internally, those become adapters over the new runtime.

## Storage Model

Keep `skill_library/<slug>/...` as the canonical layout:

- `skill_library/<slug>/SKILL.md`
- `skill_library/<slug>/metadata.json`
- `skill_library/<slug>/references/...`
- `skill_library/<slug>/examples/...`
- `skill_library/<slug>/scripts/...`

Keep `skill_library/agents/<agent>/bindings.json` as a compatibility binding input, but stop using `skill_library/agents/<agent>/<slug>/...` mirrored copies as a primary runtime source.

Add a runtime-owned internal directory:

- `skill_library/.runtime/index.json`
- `skill_library/.runtime/migrations/*.json`

## Loading Flow

Target flow:

1. Runtime discovers canonical skill roots from `skill_library/`.
2. Runtime parses frontmatter and metadata into normalized entries.
3. Runtime loads legacy bindings and migrates them into normalized binding records.
4. Runtime filters entries for the current agent and context.
5. Runtime generates prompt-visible skill catalog and matched skill metadata.
6. Compatibility tools call runtime access helpers, which enforce agent binding and path containment.

## Migration Strategy

Migration must read and preserve:

- existing `skill_library/<slug>/metadata.json`
- existing `skill_library/<slug>/bindings.json`
- existing `skill_library/agents/<agent>/bindings.json`
- existing `skill_library/agents/<agent>/<slug>/binding.json`

Migration rules:

1. Canonical source skills always come from `skill_library/<slug>`.
2. Agent mirror directories are treated as legacy artifacts only.
3. If a mirror binding exists but source binding metadata is missing, synthesize a normalized binding record from the mirror metadata.
4. On write, persist only canonical source skill files, canonical metadata, per-agent `bindings.json`, and runtime migration records.
5. Do not recreate mirrored agent skill trees after migration.

## Safety Requirements

The refactor must enforce:

1. All skill body/resource access is bounded to the canonical realpath of the selected skill root.
2. A tool call cannot read a skill that is not enabled for the current agent.
3. Resource reads reject `..`, symlink escape, and non-file targets.
4. Oversized or malformed skill roots fail closed.

## Testing Strategy

Add or update tests for:

- skill discovery from canonical roots
- frontmatter parsing and metadata normalization
- legacy binding migration
- disabled and unbound skill access rejection
- path traversal and symlink escape rejection
- prompt generation for agent-visible skills
- compatibility behavior of `SkillService` and `skill_tool`
- Finding runtime guidance generation against runtime outputs
