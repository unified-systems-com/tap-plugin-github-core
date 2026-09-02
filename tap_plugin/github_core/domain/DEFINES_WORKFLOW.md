# DEFINES_WORKFLOW

## Blurb

A repository contains a workflow definition — the edge from the thing being protected to the thing that declares what CI may do to it.

## Purpose

This is where the containment chain reaches the Actions surface. Everything above it is inventory; from here down the graph is about pipelines. It exists so a question can move in both directions: from a repository to what it will run, and from a suspicious workflow declaration back to what it can affect.

## Goals

- Scope a workflow to exactly one repository, so a finding on a declaration is attributable.
- Complete the containment walk from platform down to a workflow definition.
- Give the run→workflow join ([`EXECUTES_WORKFLOW`](EXECUTES_WORKFLOW.md)) a repository to resolve against.

## Identity

Edge id is `uuid5(ns, "edge:DEFINES_WORKFLOW__github_core:<source id>:<target id>")`. The target's id already embeds the repository (`<full_name>#<workflow_id>`), so this edge cannot cross repositories by construction.

## Boundaries

Carries **no properties**, justified: a workflow file is in a repository or it is not. The path is a field on the workflow, not a property of the containment; putting it here would derive the same fact twice and let the two disagree.

Not covered: **reusable workflow calls.** A workflow in repository A calling a workflow in repository B is a real and security-relevant relationship, and it is a different edge — [`CALLS_WORKFLOW`](CALLS_WORKFLOW.md), from the calling job, built 2026-09-02. A reader should not infer from `DEFINES_WORKFLOW` alone that a repository's CI is confined to that repository. Likewise [`TRIGGERS_WORKFLOW`](TRIGGERS_WORKFLOW.md) (`workflow_run` chains) is its own edge.

## Neutrality

**Neutral-capable.** Both endpoints are marked neutral in the corpus; a repository-defines-pipeline relationship survives in any forge and in the non-forge kernel pressure test.

## Observability

Derived from the workflow listing: `GET /repos/{o}/{r}/actions/workflows` at **`repository:actions:read`** already returns workflows *for a repository*, so the pairing is inherent in the call rather than read from a field.

The important observability caveat belongs to the target, not the edge: `actions:read` yields the workflow's existence, while `repository:contents:read` is needed for the YAML that makes it useful. A repository can therefore show a full set of `DEFINES_WORKFLOW` edges to workflow nodes that are almost entirely empty — present, correct, and uninformative. Check `configuration.raw_yaml` before concluding a workflow is simple.

## Authoritative Source

- **Source:** GitHub REST API — Actions Workflows (`GET /repos/{owner}/{repo}/actions/workflows`); Repository Contents (`GET /repos/{owner}/{repo}/contents/{path}`) for the definition body
- **Version:** REST API version `2022-11-28`
- **Retrieved:** 2026-08-27

## Prior Art

- `specs/spec-github-core-vocabulary.md` (2026-08-27) — `DEFINES_WORKFLOW` in the existing spine; `CALLS_WORKFLOW` and `TRIGGERS_WORKFLOW` recorded separately, which is what this edge deliberately does not cover.
- `git-serious-tap/docs/doc-git-serious-cicd-security-prior-art.md` §3.9 (2026-08-27) — the two-permission split between listing workflows and reading their YAML.
- GitHub REST API, version `2022-11-28` — Actions Workflows and Contents endpoints.

## Endpoints

- **Source:** `github_core__github_repository`.
- **Target:** `github_core__github_workflow`.
- **Dimensions:** `github.platform`, `github.surface: actions`, `github.observation: declaration`. The observation value marks both ends of this edge as the declared side of CI — see [`github.observation`](dimensions/github.observation.md) for why that distinction is load-bearing.
