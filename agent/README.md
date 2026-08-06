# The `dev-*` skills

A software-engineering methodology suite — the path from **idea to shipped code**, expressed as composable skills: sharpen an idea by grilling, prototype the risky parts, turn it into a spec and tracer-bullet tickets, implement each test-first, and review the diff. Supporting skills carry the vocabulary (deep modules, domain modeling), the diagnostics (bug diagnosis, research), and the plumbing (triage, wayfinding, setup, handoff, wizard, wait-what). The `dev-` prefix groups this engineering suite and distinguishes it from standalone skills (`grill-me`, `teach`, …).

> **Adoption:** every skill below was deliberately ported/adopted in a prior session, so adoption is ✅ throughout. The 🔶 *needs-review* tag is reserved for future speculative additions — the live signal lives in the **Sync** column.

## Provenance

Inspired by and ported from **[`mattpocock/skills`](https://github.com/mattpocock/skills)**, adapted for pi (the `herdr` leaf fan-out, `dev-` cross-references, and the conventions in [Intentional divergences](#intentional-divergences)).

- **Pinned at last review:** [`8b36d4fb2635b3c21998dcd8144439c9e5ba7302`](https://github.com/mattpocock/skills/commit/8b36d4fb2635b3c21998dcd8144439c9e5ba7302) (2026-08-05)
- **Last full sync review:** 2026-08-06

## Registry

| `dev-*` skill | ← upstream | Sync | Notes |
|---|---|---|---|
| dev-codebase-design | engineering/codebase-design | adapted | `DESIGN-IT-TWICE.md` uses herdr; `SKILL.md` synced |
| dev-code-review | engineering/code-review | adapted | step 4 rewritten for herdr; retains "PRD" wording |
| dev-diagnosing-bugs | engineering/diagnosing-bugs | synced | name refs only |
| dev-domain-modeling | engineering/domain-modeling | synced | byte-identical body |
| dev-grill-with-docs | engineering/grill-with-docs | synced | name refs only |
| dev-handoff | productivity/handoff | synced | byte-identical body |
| dev-implement | engineering/implement | synced | name refs only |
| dev-improve-architecture | engineering/improve-codebase-architecture | adapted | explore + design steps use herdr |
| dev-prototype | engineering/prototype | adapted | logic branch kept on **terminal TUI** by choice (upstream → HTML demo) |
| dev-research | engineering/research | adapted | leaf fan-out via herdr; "establish what's current first" intro kept |
| dev-resolving-merge-conflicts | engineering/resolving-merge-conflicts | synced | byte-identical body |
| dev-router | engineering/ask-matt | adapted | names + reworded (codebase vs working dir, smart-zone 120k, `/dev-handoff` vs `/compact`) |
| dev-setup | engineering/setup-matt-pocock-skills | synced | name refs only |
| dev-tdd | engineering/tdd | synced | pulled the `/dev-codebase-design` pointer on 2026-08-06 |
| dev-to-spec | engineering/to-spec | adapted | retains "(you may know this as a PRD)" gloss |
| dev-to-tickets | engineering/to-tickets | synced | name refs only |
| dev-triage | engineering/triage | adapted | grilling kept "one question at a time" (upstream: round-by-round) |
| dev-wait-what | productivity/wait-what | adapted | faithful port + `CONTEXT.md` "(if it exists)" hedge |
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
- **Terminal prototype** — `dev-prototype/LOGIC.md` stays a terminal TUI (upstream moved to a shareable HTML demo); kept for language fidelity.
- **One-at-a-time grilling** — `triage` (and `grill-me`) say "one question at a time"; upstream moved to "round-by-round".
- **`CONTEXT.md` conditional** — `dev-wait-what` adds "(if it exists)".

## Also from mattpocock (non-`dev-`)

These share the provenance but aren't part of the `dev-` suite: `grill-me` (← productivity/grill-me), `teach` (← productivity/teach).

## Upstream skills not adopted

Available to pull if ever wanted: `productivity/grilling`, `productivity/to-questionnaire`, `productivity/writing-for-agents`. The `in-progress/` and `misc/` buckets also exist upstream but aren't tracked here.
