# Contract Format

Contracts live in `docs/contracts/` and use sequential numbering: `0001-slug.md`, `0002-slug.md`, etc. Create the directory lazily — only when the first contract is needed.

A **contract** records obligations future specs must obey, each with the evidence that settled it. An ADR records a decision and its why. Rationale stays in the ADR; the contract states what future work must do.

## When to create a contract

All three must hold:

1. **Cross-spec** — a future spec must obey the decision without reopening it.
2. **Nowhere else authoritative** — no schema, interface, or config already encodes it.
3. **Checkable** — compliance follows from the obligation's wording.

Everything else stays in the spec. A decision whose wider scope was never stated stays in the spec. Capture only what the discussion settled or a prototype validated — research supplies evidence, not approval. An unsettled cross-spec decision is a gap to report to the user.

## Template

```md
# {Title}

**Status:** Active

## Applicability

{The work this contract governs.}

## Obligations

- {One obligation per bullet, precise enough to check. An obligation that
rests on a prototype, research, or external documentation carries its source:}

  - **Evidence:** {what the source established — research path and heading,
prototype branch, commit and path, versioned URL, or issue link.}
```

Supersession replaces the status line only:

```md
**Status:** Superseded by [{NNNN — Title}](./NNNN-slug.md)
```

## Rules

- **Semantically immutable.** A changed obligation gets a new number; the old contract points to it. Wording and link repairs edit in place.
- **Prospective.** A successor binds new specs; a published spec stays pinned to the contract it references. Migrating it takes an explicit amendment.
- **Conflicts.** Explicit replacement: write the successor and update the map. A decision that contradicts an active contract without explicit replacement: stop and report the conflict.
- **Resolvable evidence.** Every cited locator resolves — verify paths and git revisions before publishing the spec. Each source states what it established; a bare link leaves the implementer guessing.

## Discovery — the contract map

`docs/contracts/CONTRACT-MAP.md` routes to contracts. Create it with the first contract, add one pointer to `AGENTS.md`, and leave that pointer alone from then on:

```md
Contracts — specs select through [the contract map](docs/contracts/CONTRACT-MAP.md); implementation and review load only those selected.
```

The map carries one trigger and one link per line, active contracts only — supersession removes the entry:

```md
# Contract Map

- [0012 — Authentication callback](0012-authentication-callback.md) — login, callbacks, sessions, tokens
```

When distinct areas emerge, split into per-area maps the root map routes to:

```md
# Contract Map

- [Frontend](frontend.md) — frontend surfaces
- [Identity](identity.md) — login, sessions, tokens
```

## Numbering

Scan `docs/contracts/` for the highest existing number and increment by one.
