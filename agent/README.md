# The `dev-*` skills

A software-engineering methodology suite — the path from **idea to shipped code**, expressed as composable skills: sharpen an idea by grilling, prototype the risky parts, turn it into a spec and tracer-bullet tickets, implement each test-first, and review the diff. Supporting skills carry the vocabulary (deep modules, domain modeling), the diagnostics (bug diagnosis, research), and the plumbing (triage, wayfinding, setup, handoff, wizard, wait-what). The `dev-` prefix groups this engineering suite and distinguishes it from standalone skills (`grill-me`, `teach`, …).

> **Adoption:** every skill below was deliberately ported/adopted in a prior session, so adoption is ✅ throughout. The 🔶 *needs-review* tag is reserved for future speculative additions — the live signal lives in the **Sync** column.

## Provenance

Inspired by and ported from **[`mattpocock/skills`](https://github.com/mattpocock/skills)**, adapted for pi (the `herdr` leaf fan-out, `dev-` cross-references, and the conventions in [Intentional divergences](#intentional-divergences)).

- **Pinned at last review:** [`4ae2317ddb22b046e26455e4f9c9b3aaaf62212d`](https://github.com/mattpocock/skills/commit/4ae2317ddb22b046e26455e4f9c9b3aaaf62212d) (2026-11-10)
- **Last sync:** 2026-11-10 — ported `pr`, `retro`, `to-questionnaire`; migrated the suite `CONTEXT.md` → `GLOSSARY.md` (upstream #1120); root glossary `git mv`'d; `dev-setup` offers legacy migration in target repos

## Registry

| `dev-*` skill | ← upstream | Sync | Notes |
|---|---|---|---|
| dev-codebase-design | engineering/codebase-design | adapted | `DESIGN-IT-TWICE.md` uses herdr; `SKILL.md` synced |
| dev-code-review | engineering/code-review | adapted | step 4 rewritten for herdr; retains "PRD" wording |
| dev-diagnosing-bugs | engineering/diagnosing-bugs | synced | name refs only |
| dev-domain-modeling | engineering/domain-modeling | synced | upstream body + description pulled verbatim (2026-11-10) |
| dev-grill-with-docs | engineering/grill-with-docs | synced | name refs only |
| dev-handoff | productivity/handoff | synced | byte-identical body |
| dev-implement | engineering/implement | synced | name refs only |
| dev-improve-architecture | engineering/improve-codebase-architecture | adapted | explore + design steps use herdr |
| dev-prototype | engineering/prototype | adapted | logic branch kept on **terminal TUI** by choice (upstream → HTML demo) |
| dev-pr | engineering/pr | synced | faithful port; model-invoked; HumanLayer `show-me` credits kept |
| dev-research | engineering/research | adapted | leaf fan-out via herdr; "establish what's current first" intro kept |
| dev-retro | engineering/retro | adapted | Skill-tool → pi skill run; session logs pointed at `~/.pi/agent/sessions/` |
| dev-resolving-merge-conflicts | engineering/resolving-merge-conflicts | stale | upstream **removed** this skill (#1120); local copy kept pending decision |
| dev-router | engineering/ask-matt | adapted | surgical merge 2026-11-10 (`pr`/`retro`/`to-questionnaire`/`wait-what`, read-first preamble, 150k smart zone); `grilling`/`implement-spec`/phase-boundaries deliberately not ported |
| dev-setup | engineering/setup-matt-pocock-skills | stale | GLOSSARY rename + legacy-file migration offer pulled (2026-11-10); later upstream template changes unreviewed |
| dev-tdd | engineering/tdd | synced | pulled the `/dev-codebase-design` pointer on 2026-08-06 |
| dev-to-questionnaire | productivity/to-questionnaire | synced | faithful port |
| dev-to-spec | engineering/to-spec | adapted | retains "PRD" gloss; adds durable-contract capture (`CONTRACT-FORMAT.md`) |
| dev-to-tickets | engineering/to-tickets | synced | name refs only |
| dev-triage | engineering/triage | adapted | grilling kept "one question at a time" (upstream: round-by-round) |
| dev-wait-what | productivity/wait-what | adapted | faithful port + `GLOSSARY.md` "(if it exists)" hedge |
| dev-wayfinder | engineering/wayfinder | adapted | underscore emphasis style; name refs |
| dev-wizard | engineering/wizard | synced | faithful port; `template.sh` byte-identical; model-invoked |

**Sync legend:** `synced` = matches upstream (modulo `dev-`/name changes) · `adapted` = intentionally diverged (reason noted) · `stale` = upstream has changes not yet pulled.

## How to re-sync from upstream

```bash
git clone --depth 1 https://github.com/mattpocock/skills.git /tmp/mp-skills
# Compare each skill's .md body (strip YAML frontmatter) against its upstream origin.
# Skip agents/openai.yaml — platform-specific, not ported.
# Classify each diff: name-only → synced; herdr/ecosystem → adapted (keep); new upstream content → review.
```
Commit date per upstream file settles "changed since the pin": `api.github.com/repos/mattpocock/skills/commits?path=skills/<bucket>/<skill>/SKILL.md`.

## Intentional divergences

Do **not** "fix" these back to upstream during a re-sync — they're deliberate:

- **`herdr` fan-out** — `code-review`, `research`, `improve-architecture`, `codebase-design` (`DESIGN-IT-TWICE.md`) replace upstream's subagent/`Agent`-tool calls with the `herd` skill's leaf mechanism (pi has no subagent tool).
- **`dev-` names** — all cross-references use `/dev-*` (upstream uses bare names).
- **PRD terminology** — `code-review`, `to-spec` keep "PRD" where upstream renamed to "spec".
- **Durable contracts** — `to-spec` extracts cross-spec decisions into numbered contracts under `docs/contracts/` (see `dev-to-spec/CONTRACT-FORMAT.md`); upstream has no equivalent.
- **Terminal prototype** — `dev-prototype/LOGIC.md` stays a terminal TUI (upstream moved to a shareable HTML demo); kept for language fidelity.
- **One-at-a-time grilling** — `triage` (and `grill-me`) say "one question at a time"; upstream moved to "round-by-round".
- **`GLOSSARY.md` conditional** — `dev-wait-what` adds "(if it exists)".

## Also from mattpocock (non-`dev-`)

These share the provenance but aren't part of the `dev-` suite: `grill-me` (← productivity/grill-me), `teach` (← productivity/teach), `writing-for-agents` (← productivity/writing-for-agents).

## Upstream skills not adopted

Available to pull if ever wanted: `productivity/grilling`. (`pr`, `retro`, `to-questionnaire` were adopted 2026-11-10.) The `in-progress/` and `misc/` buckets also exist upstream but aren't tracked here.
