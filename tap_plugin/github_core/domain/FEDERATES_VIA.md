# FEDERATES_VIA

## Blurb

A GitHub repository federates into AWS through an IAM OIDC provider — a **derived** cross-grid link, inferred from a matched value rather than read from either side.

## Purpose

OIDC federation replaced long-lived cloud keys in CI, and it moved the trust decision somewhere neither side can see alone. GitHub knows it mints tokens for a repository; AWS knows it trusts an issuer and a subject pattern. Nothing in GitHub's API says which cloud account trusts you, and nothing in AWS's says which repository is on the other end of the claim it matches.

The grid is the only place both halves are present, so this edge is the join. It chains: `repository --FEDERATES_VIA--> oidc_provider --FEDERATES_INTO--> deploy_role`. Following it end to end answers a question neither provider can: **which repository can assume which cloud role.**

The corpus notes we are ahead of the field here — the published GitHub graph schemas surveyed have no OIDC issuer at all.

## Goals

- Join the two halves of a federation that neither platform can see whole.
- Make the derivation auditable, so an inferred edge is never mistaken for an observed one.
- Do it conservatively — a wrong edge here asserts a trust relationship that does not exist.

## Identity

Edge id is `uuid5(ns, "edge:FEDERATES_VIA__github_core:<source id>:<target id>")`. Resolved during the **enrichment** phase from the grid-link manifest's structural-constant rule, after both endpoints are on the grid.

## Boundaries

This edge is **derived and not hotlink-backed**, and that distinction is the whole boundary. A hotlink is a link the source system asserts; this is a link *we* concluded. It carries provenance properties for exactly that reason:

- `link_rule` — **which** manifest rule matched.
- `matched_value` — **what** the rule keyed on.

They are `dependentRequired` on each other, so an edge can carry both or neither, never a rule with no evidence. This is the same provenance pair `identity_core`'s `TRUSTS_ISSUER` uses — one shape across the enrichment engine, so a consumer learns it once.

Deliberately not covered: the **subject-claim conditions** on the AWS side — which `sub` patterns the role's trust policy actually accepts. That is the property that decides whether federation is scoped to one branch or open to every ref in the repository, and it lives in AWS's trust policy, on the far side of this edge. Reading this edge as "this repository can assume that role" **overstates it**: it says the federation path exists, not that a given ref satisfies the condition.

Also not covered: other cloud providers. The target union is AWS-only today.

## Neutrality

**Cross-plugin by nature.** The source is GitHub-specific, the target belongs to `aws_core`, and the generic half of the concept — the issuer and the trust edge — already moved to the neutral `identity_core` substrate. This edge is the vendor-to-vendor connector that remains.

## Observability

Neither endpoint's API returns this relationship; it is **inferred at enrichment time** from the canonical Actions issuer URL `token.actions.githubusercontent.com` appearing on both sides. Matching is **exact-only**, and a failure to resolve is a **warning, not an error** (`req-github-core-grid-links`): a missing edge degrades the picture, while a wrong one asserts a trust relationship that does not exist.

The link was verified end to end against a real GitHub-to-AWS federation (`req-github-core-grid-links`), which is worth stating because a derived edge that has only been unit-tested is a hypothesis.

**Not observable at all from GitHub:** the cloud-side trust policy, its subject conditions, and its permissions — the half that decides what the federation is worth. The GitHub half of the contract that *is* readable is OIDC subject customization (`GET /repos/{o}/{r}/actions/oidc/customization/sub`, Administration read), which changes what the claim contains; it is not collected today.

## Authoritative Source

- **Source:** GitHub Actions OIDC documentation — the canonical issuer `token.actions.githubusercontent.com` and the `repo:<owner>/<repo>:...` subject-claim format; AWS IAM OIDC identity-provider documentation for the trust side
- **Version:** REST API version `2022-11-28` (GitHub side); AWS IAM OIDC provider model as of 2026-08-27
- **Retrieved:** 2026-08-27

## Prior Art

- `specs/spec-github-core-vocabulary.md` (2026-08-27) — records `identity_core__oidc_issuer` as a place we are ahead of the published GitHub graph schemas, which model no issuer at all.
- `git-serious-tap/docs/doc-git-serious-cicd-security-prior-art.md` §3.9–3.10 (2026-08-27) — the OIDC customization endpoint, and the condition that `id-token: write` should be confined to publish jobs bound to an environment, with the note that cloud-side conditions are not observable.
- `specs/spec-github-core-v0.md` `req-github-core-grid-links` (2026-08-27) — exact-only matching, warn-only failure, and the end-to-end verification.
- GitHub Actions OIDC documentation and AWS IAM OIDC identity providers, both read 2026-08-27.

## Endpoints

- **Source:** `github_core__github_repository`.
- **Target:** `aws_core__aws_iam_oidc_provider` — cross-plugin, resolved only when `aws_core` is installed; enrichment degrades rather than aborting when a rule's vocabulary is absent.
- **Properties:** `link_rule` and `matched_value`, mutually `dependentRequired`, `additionalProperties: false`. The derived-link provenance pair.
- **Dimensions:** `github.platform`, `github.observation: declaration`. No `github.surface` — federation is not confined to the Actions surface. `declaration` is right despite this edge being *derived*: the dimension records which side of CI the fact describes (a standing trust path), not how confidently we know it — that is what `link_rule` and `matched_value` are for.
