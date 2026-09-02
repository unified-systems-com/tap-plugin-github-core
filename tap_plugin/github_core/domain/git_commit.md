# Git Commit

## Blurb

The commit a ref resolves to — who authored and committed it as GitHub observed them, and whether its signature verified. The object a required-signatures rule actually checks.

## Purpose

A ruleset can require signed commits (`required_signatures`), a branch can point at a commit, and until now the two never met: the ref carried a `head_sha` string and nothing on the grid knew whether that commit was signed, by whom, or whether GitHub had verified the key. This node is the convergence the corpus's ranking names — "a commit joins refs to signatures" — and the reason it was pulled forward from the friends tier (github-core#57): signature state is a **ruleset input**, and a gate view that cannot see it is answering "is the protected branch clean" from the rule text alone.

It is a deliberately narrow slice. No message, no tree, no parents, no history: the grid's field history on `git_ref.head_sha` is where movement lives, and a full commit graph is a different product. What is here is what a signature question and an identity question need, and nothing that would tempt anyone to treat this node as a mirror of the repository.

## Goals

- Let a ruleset's `required_signatures` be checked against what is on the ref, not merely declared.
- Record identity **as observed** — the login GitHub resolved, or the fact that it resolved none.
- Hold the signature in three states, so "unsigned" and "could not read" and "verified" are never the same row.
- Be the node a SHA-pinned `USES_ACTION` or a run's `head_sha` can later resolve to.

## Identity

Natural key: **the SHA alone.** Entity id is `uuid5(ns, "github_core__git_commit:<sha>")`, lower-cased.

Platform-global rather than repository-scoped, and the reason is what git is: a commit is content-addressed and identical wherever it lives. A per-repository key would mint one node per fork carrying the same commit, and would lose the one join that makes the node worth having later — a SHA-pinned action reference or a run's head resolving to a commit *some in-scope repository carries*, which is the fork check the `USES_ACTION` article had to leave open. Signature state is a property of the commit and of GitHub's key/user relationship, not of the repository; the same commit reports the same state from every repository that has it.

## Boundaries

Deliberately **not** covered:

- **The message, tree and parents.** Not history, not content. A commit graph is a different vocabulary and a different collection cost.
- **Commits that are not at a ref's head.** Only what the config-layer refs query resolves is collected. A run's `head_sha`, a rule suite's `after_sha`, an artifact's `head_sha` and a SHA-pinned `uses:` all name commits this node *could* represent; joining them is a follow-on, not built, and stated so a query joining on `sha` today knows it will miss commits no ref points at.
- **The signature payload.** `signature.payload` and `signature.signature` (the armored blocks) are bulk with no question behind them. Not stored.
- **Who the signer *is*.** `signer_login` is a GitHub login. Whether that account is the committer, a bot, or someone else is a query over `identity_core`, not a field here.
- **The tag object.** For an annotated tag this node is the commit the tag points at; the tag object's own signature (a signed tag) is a separate thing and is not collected. `git_ref.target_sha` still records the tag object.

## Neutrality

**Yes** — the corpus marks the revision concept neutral (7 sources), and a commit is a git object before it is a GitHub one. What is GitHub's is the verification vocabulary: `signature_state` values are GitHub's enum, and `signed_by_github` (web-flow commits signed with GitHub's own key) has no meaning elsewhere. Those would travel as properties in a neutral extraction; the node would not change.

## Observability

Populated from the **config-layer GraphQL query** at **`repository:contents:read`** — the `refs` source's own permission — by a `CommitSlice` fragment on every ref's target (and on `Tag.target` for annotated tags). Scalar fields on nodes the query already requests: **measured on 2026-09-02 at no additional rate-limit cost** (`rateLimit.cost: 1` for a repository's refs with the full fragment), so this node widens neither the credential nor the request budget.

Observed shapes, by executed call against `unified-systems-com/tap` on 2026-09-02:

- A signed head: `signature: {__typename: SshSignature, isValid: true, state: VALID, wasSignedByGitHub: false, signer: {login: notgeorge}}`, author and committer each with `user: {login}` resolved.
- An unsigned head: **`signature: null`**. This is an observed value — GitHub answered the field — and it lands as `signature_state: unsigned`, `signature_valid: null`, `signature_kind: ""`. Null, not false: "not valid" would be a claim about a signature that does not exist.
- A tag: the same fragment on the commit the tag resolves to.

**Three states, never two.** Signed (GitHub's own `state`, lower-cased: `valid`, `unknown_key`, `bad_email`, `unverified_email`, `no_user`, `expired_key`, …), unsigned (`signature: null`), and **not observable** — a field the credential could not read arrives in the response's `errors[]`, the config layer surfaces it as `GRAPHQL_FIELD_DEGRADED`, and the ref carries no commit slice, so **no node is emitted**: a row of empty strings would read as an unsigned commit by someone nobody could name. A **repos-only scope** runs no config-layer query and therefore collects no commits, as it collects no refs; the ref source already states that.

**Author and committer as observed.** `user: null` on a `GitActor` means GitHub resolved the email to no account — an observed absence, stored as an empty login. It is not the same as "we did not ask".

**Absence shape** (github-core#14): **Shape D, derived absence** — a shared, content-addressed node relevant while any ref points at it; never tombstoned on its own observation. Its inbound [`POINTS_AT`](POINTS_AT.md) is **Shape G, recomputed**: a ref that moves points at a new commit, the old edge lingers under today's additive-only collection, and reconciliation — not a property — is the fix.

## Authoritative Source

- **Source:** GitHub GraphQL API — `Commit` (`committedDate`, `authoredDate`, `author`, `committer`, `signature`), `GitActor` (`name`, `email`, `user`), `GitSignature` interface (`isValid`, `state`, `wasSignedByGitHub`, `signer`) with `GpgSignature` / `SmimeSignature` / `SshSignature`; `GitSignatureState` enum; the repository rule type `required_signatures`
- **Version:** GraphQL schema as published by `octokit/graphql-schema` at the commit pinned in `github_openapi_extract.json` (refreshed 2026-09-02)
- **Retrieved:** 2026-09-02

## Prior Art

- `specs/spec-github-core-vocabulary.md` (2026-08-27) — `git_commit`, friends tier, 7 sources as *revision*, "narrow slice only"; `POINTS_AT` `{observed_at}`, whose property is dropped here as a duplicate of the substrate.
- unified-systems-com/tap-plugin-github-core#57 (2026-09-02) — the bake issue: the measured cost, the null-signature observation, the SHA-alone key.
- [`git_ref`](../models/git_ref.py) (2026-08-27) — the node this one completes; `head_sha` field history is the movement record.
- [`github_ruleset`](github_ruleset.md) — the `required_signatures` rule this node lets a view check.

## Fields

- `sha` — the commit id, and the natural key. Lower-cased 40-hex; the schema allows an abbreviated form only so a node can be referenced before its full id is known.
- `committed_date` — when the commit was made, per the committer line.
- `authored_date` — when the change was authored; differs from `committed_date` on a rebase or amend, which is itself a signal about who last touched it.
- `author_name` — the author line's name, as written in the commit.
- `author_email` — the author line's email, as written. What GitHub resolves to an account, or fails to.
- `author_login` — the account GitHub resolved the author email to; empty when it resolved to none (observed-absent).
- `committer_name` — the committer line's name.
- `committer_email` — the committer line's email.
- `committer_login` — the account GitHub resolved the committer email to; `web-flow` for browser-made commits.
- `signature_kind` — `gpg`, `smime` or `ssh` from the signature's GraphQL type; empty when unsigned.
- `signature_state` — GitHub's verification state, lower-cased, or `unsigned` when GitHub returned no signature object. The field a `required_signatures` view reads.
- `signature_valid` — GitHub's `isValid`; **null when unsigned**, never false.
- `signer_login` — the account whose key produced a verified signature; empty when unsigned or when GitHub could not attribute it.
- `signed_by_github` — the signature is GitHub's own web-flow key. A merge made in the browser is signed, verified, and not by any human's key.
- `configuration` — reserved; empty.
- `tags` — TAP's tag map.
