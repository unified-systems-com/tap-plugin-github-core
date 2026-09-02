# POINTS_AT

## Blurb

A ref resolves to a commit — the branch head, or the commit an annotated tag's object points at.

## Purpose

`git_ref.head_sha` was a string; this edge makes it a hop. It exists so a ruleset can be walked to what it protects and then to what is actually there — `PROTECTS` to the repository, `HAS_REF` and `EVALUATED_ON_REF` to the ref, `POINTS_AT` to the commit whose signature state a `required_signatures` rule is about. It is the last link in the gate question, and it is exact: the config layer returns the commit *on* the ref, not a match by name.

## Goals

- Turn the ref-to-commit relationship into a traversal.
- Keep it exact — derived from the same response that mints the ref, never matched.
- Carry nothing the grid already keeps.

## Identity

Edge id is `uuid5(ns, "edge:POINTS_AT__github_core:<ref id>:<commit id>")`. A ref that moves points at a new commit and gets a new edge.

## Boundaries

Carries **no properties**, and the justification is specific because the corpus proposed one. `{observed_at}` would record when the pointing was seen; every write already carries batch provenance, and `git_ref.head_sha` has field history that records every move with its batch. Putting a timestamp here would be a second derivation of a fact the substrate owns, and the two would disagree the first time a collection was re-run.

Not covered:

- **The tag object.** For an annotated tag the target is the commit the tag resolves to (`git_ref.head_sha`), not the tag object (`git_ref.target_sha`). A signed tag's own signature is not collected.
- **The previous commit.** The edge to the commit a ref used to point at lingers after a move, because collection is additive-only (github-core#14). That is Shape G — a relation that was re-derived, not one that ended — and reconciliation is where it is fixed; a `current` flag here would be a stale write waiting to happen.

## Neutrality

**Yes**, with both endpoints: a ref pointing at a commit is git.

## Observability

Derived from the config-layer refs query at **`repository:contents:read`** — the same response that mints the ref, so the edge costs nothing. Emitted only when the ref carried a commit slice; a degraded commit field, or a repos-only scope (which runs no config layer), yields refs with no commit and no edge, stated by the config layer's own degradation notes rather than by silence.

Measured 2026-09-02: the commit fragment added no rate-limit cost to the refs query.

**Absence shape** (github-core#14): **Shape G, recomputed.** A ref moving is not this edge ending; it is the relation being re-derived. Wholesale replacement per ref over a proven scope is the reconciliation this edge wants, never a tombstone read as "the branch no longer points anywhere".

## Authoritative Source

- **Source:** GitHub GraphQL API — `Ref.target` (`GitObject`: `Commit`, or `Tag` whose `target` is the commit); the `refs` connection on `Repository`
- **Version:** GraphQL schema as published by `octokit/graphql-schema` at the commit pinned in `github_openapi_extract.json` (refreshed 2026-09-02)
- **Retrieved:** 2026-09-02

## Prior Art

- `specs/spec-github-core-vocabulary.md` (2026-08-27) — `POINTS_AT` `{observed_at}`; the property is dropped here as a duplicate of batch provenance and field history.
- unified-systems-com/tap-plugin-github-core#57 (2026-09-02) — the bake issue.
- [`HAS_REF`](../edges/HAS_REF.edge.json) — the containment edge above this one; "ref movement is not a property of this edge — it is field history on the ref's head_sha" is the same ruling applied there.

## Endpoints

- **Source:** `github_core__git_ref` — the branch or tag.
- **Target:** `github_core__git_commit` — the commit it resolves to, as observed in this repository (shared across the repository's refs).
- **Dimensions:** `github.platform`, `github.surface: git`, `github.observation: declaration`, plus the repository's `github.owner` / `github.repo` from the ref's own dimensions; both ends are repository-scoped.
