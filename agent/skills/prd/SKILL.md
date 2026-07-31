---
name: prd
description: Create and iterate a human-facing Product Requirements Document at .planning/prd-<slug>.md — aligns people on the what and why (problem, users, goals, scope). Owns PRD content and structure; delegates the interview to grill-me. Invoke with /skill:prd [slug-or-topic]; never auto-loaded.
disable-model-invocation: true
---

A PRD aligns people on the **what** and **why** of an initiative — the problem, who it's for, what success looks like, and the scope. This skill owns the doc's **content and structure**; the interviewing is delegated to **grill-me** (`/skill:grill-me`) — load its `SKILL.md` and apply its method; do not reinvent interviewing here.

A section is written only when it is **resolved** — every decision feeding it settled, the same bar grill-me uses to close a branch. **Resolved** is the gate that runs through this whole skill: grill to resolution, ground, then write.

## File

- Lives at `.planning/prd-<slug>.md` (many PRDs, flat; kebab-case slug from the feature name).
- `.planning/` is git-tracked — git is the change history, so keep no in-doc changelog.

## The section list (single source of truth)

The same list drives **what grill-me must settle** and **what the doc contains**. Backbone sections are required; optionals are included when the PRD warrants them and otherwise omitted — no empty filler.

**Front-matter** (YAML): `title`, `status` (`draft` | `in-review` | `approved` | `shipped` | `obsolete`), `version`, `owner`, `date` (created), `last-updated`.

**Backbone — every PRD resolves these:**
1. **Problem & evidence** — the situation, the problem, and **evidence** it's real (data, quotes, frequency). *No evidence, no problem — it's a hunch.*
2. **Target users** — who specifically has this problem.
3. **Goals & success metrics** — what success looks like, **measurable**: leading and lagging indicators. *"Improve UX" is not a metric.*
4. **Non-goals** — what we are explicitly **not** doing. The discipline that keeps scope honest.
5. **Solution / requirements** — what we're building: capabilities, use cases, or user stories.
6. **Open questions** — unresolved decisions carried explicitly, so they don't masquerade as silent assumptions.

**Optional — include when relevant:**
7. **Risks & assumptions** — what could break, what we're betting on.
8. **Timeline / milestones** — when, and the checkpoints.
9. **Rollout & migration** — how it ships (flags, migration steps, comms).

## Workflow

1. **Target.** `/skill:prd <slug-or-topic>`: if `.planning/prd-<slug>.md` exists, **resume** it — load the file first; never silently overwrite. No argument → list existing PRDs and ask whether to resume one or start fresh (and pick a slug).
   - *Done when:* exactly one target file is settled.

2. **Scaffold.** Create `.planning/prd-<slug>.md` with every header above plus front-matter (`status: draft`, `version: 0.1`, today's `date`). Unresolved sections carry a one-line `TBD — pending <dimension>` so gaps stay visible.
   - *Done when:* the file exists with all backbone headers present and every front-matter field populated (content sections as TBD).

3. **For each dimension, in dependency order** — Problem & evidence → Target users → Goals & success metrics → Solution/requirements → Non-goals (Open questions accumulate throughout; optionals after Solution):
   a. **Grill to resolution.** Apply `grill-me` (`/skill:grill-me`) — load its `SKILL.md` and run its relentless method on this one dimension until every feeding decision is settled.
   b. **Ground, if implementation-adjacent** (Solution, Risks, Rollout). Where a codebase or existing `.planning/` doc is relevant, read it and **cite what you read** — name the file or area — so the user can correct a misread. Product-level dimensions (problem, users, goals) stay sourced from the user. Nothing to read → fall back to grill.
   c. **Write the section.** Replace its `TBD` with the resolved content. Write the moment the dimension resolves — incrementally, in document order as each gate closes (sections fill out of order; that's correct). Revise a written section only when later grilling changes it.
   - *Done when:* the dimension is resolved, grounded (or stated that nothing exists), and its section written.

4. **Close.** The PRD is draft-complete when **no backbone section is a `TBD`** and optionals are either filled or marked not-applicable. That is a *signal* the doc may leave `draft` — flip `status` only when the user says so.
   - *Done when:* zero backbone TBDs; every optional resolved one way or the other.

## Iteration (re-invoke on an existing file)

- Resume by slug. Grill **only the TBD sections** toward the completion criterion.
- Revise an already-resolved section **only on the user's explicit request** ("the metrics changed") — leave settled ground alone otherwise.
- On any substantive change, bump front-matter `version` and `last-updated`.
- `status` transitions are user-driven.
