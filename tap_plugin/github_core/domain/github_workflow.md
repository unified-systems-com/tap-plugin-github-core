# GitHub Workflow

## Blurb

A workflow **as written** — the YAML file under `.github/workflows/` that declares what CI is allowed to do, which is where every privilege decision in CI is actually made.

## Purpose

A workflow definition is the highest-value artefact this plugin collects, and the reason is asymmetric: the *execution* records tell you what happened once, while the *declaration* tells you what can happen every time. `permissions:`, `runs-on:`, which trigger fires, whether a pull-request head is checked out while secrets are in scope, whether an action is pinned to a SHA — all of that lives in the file, and none of it is returned by the runs API.

So this node keeps the parsed YAML, in full, including the raw text (`configuration.raw_yaml`, `req-github-core-workflow-parse`). That is deliberate: the workflow file is the single richest source in the domain, and re-fetching it per question is worse than holding it.

## Goals

- Hold the declared side of CI, so a control question can be answered without waiting for a run.
- Keep the raw YAML alongside the parsed form, so a later parser improvement can be applied to already-collected data.
- Be the anchor a run points back at ([`EXECUTES_WORKFLOW`](EXECUTES_WORKFLOW.md)), so execution and declaration are joinable.

## Identity

Natural key: **`<full_name>#<workflow_id>`** — the repository's `owner/repo` plus GitHub's numeric workflow id. Entity id is `uuid5(ns, "github_core__github_workflow:<full_name>#<workflow_id>")`.

The numeric id rather than the file path, because a workflow keeps its id when the file is renamed or moved and that continuity is the more useful fact — a renamed workflow is the same workflow. The repository prefix is required because the numeric id is only unique within a repository.

`path` is carried as a field and is *not* identity, which is what lets a path change be observed as a change rather than as a new workflow appearing and an old one vanishing.

## Boundaries

Deliberately **not** covered:

- **The declared job.** This is the largest known gap in the model and it is named as such. The corpus rules `workflow_job` — the job **as written**, carrying its own `permissions:`, `runs-on:`, `if:` and checkout ref — into the *self* tier as "the largest gap": roughly 20 of 35 surveyed incidents need it, and it is the anchor for every conjunction query. Until it exists, that structure lives inside this node's un-schema'd `configuration` blob, which cannot be queried the way a node can.
- **Steps.** The corpus rejects `step` as a node on the node test: nothing points at a step, it is an ordinal position inside a job. Revisit only if an edge genuinely needs a step as an endpoint.
- **Triggers.** Rejected as a node — a trigger has no identity across observations. A field on the workflow.
- **Actions used.** `github_action` and `USES_ACTION` (with `pin_kind`, `pinned_sha`, `declared_ref`, `resolves_to_fork`) are corpus concepts at the *self* tier, not built. Today a `uses:` reference is text inside `configuration`.
- **Secret references.** `REFERENCES_SECRET` and its adjudication properties are corpus concepts; `req-github-core-backlog-references` is the backlog entry here. Deferred, not forgotten.

## Neutrality

**Yes.** The corpus marks the pipeline-definition concept neutral, on 12 sources: every CI system has a declared pipeline, and the kernel pressure test populates one from a project with no forge at all. What is *not* neutral is the contents of `configuration` — `permissions:`, `runs-on:` and the trigger vocabulary are GitHub Actions' own — so a neutral extraction would take the node and leave the blob's schema behind.

## Observability

Two sources, two permissions, and they are not interchangeable:

- **The workflow list** — `GET /repos/{o}/{r}/actions/workflows` at **`repository:actions:read`** — yields `workflow_id`, `path`, `name`, `state`, `html_url`. Metadata only.
- **The YAML itself** — `GET /repos/{o}/{r}/contents/.github/workflows/{file}`, base64-decoded inline, at **`repository:contents:read`**. This is the rich half, and it needs a permission the list does not.

A credential with `actions:read` but not `contents:read` therefore yields workflow nodes that are real but nearly empty — the failure mode is a plausible-looking inventory with every interesting field blank. Worth checking before concluding a repository has clean workflows.

**GraphQL does not help here.** GitHub's GraphQL API serves the configuration layer; the Actions operation layer — runs and jobs — is REST-only, and there is no GraphQL workflow-definition surface to substitute. Collecting workflows means REST plus the contents API.

**Not observable at all:** the *effective* per-job permissions actually granted at run time. The declaration is visible; the resolved grant is not returned by the runs API. Cloud-side OIDC trust conditions (the other half of any `id-token: write` federation) are invisible from GitHub entirely — they live in the cloud provider's account, which is exactly why [`FEDERATES_VIA`](FEDERATES_VIA.md) exists as a cross-grid link.

## Authoritative Source

- **Source:** GitHub REST API — Actions Workflows (`GET /repos/{owner}/{repo}/actions/workflows`) and Repository Contents (`GET /repos/{owner}/{repo}/contents/{path}`); GitHub Actions workflow-syntax reference for the YAML grammar parsed into `configuration`
- **Version:** REST API version `2022-11-28`
- **Retrieved:** 2026-08-27

## Prior Art

- `specs/spec-github-core-vocabulary.md` (2026-08-27) — 12 sources for the pipeline-definition concept; records the `workflow_job` gap and the rejection of `step` and `trigger` as nodes.
- `git-serious-tap/docs/doc-git-serious-vocab-platform-models.md` (2026-08-27) — the platform-model survey, including two published GitHub graph schemas that model the declared side only.
- `git-serious-tap/docs/doc-git-serious-cicd-security-prior-art.md` §3.10 (2026-08-27) — fifteen workflow-level observable conditions, all of which read from this node's YAML.
- GitHub Actions workflow syntax reference and REST API version `2022-11-28` — the grammar and endpoints `req-github-core-workflow-parse` parses against.

## Fields

- `full_name` — the owning repository's `owner/repo`. Half the natural key, and the reason a workflow node can be minted from a reference before the repository is collected.
- `workflow_id` — GitHub's numeric workflow id, the other half of the key. Nullable, because a workflow can be known from a file before its id is fetched.
- `path` — the file path under `.github/workflows/`. Deliberately **not** identity, so a move is observable as a change on a stable node rather than as a deletion plus a creation.
- `name` — the workflow's declared `name:`, or its path when it declares none. What a human recognises it by; not stable enough to key on.
- `state` — `active`, `disabled_manually`, `disabled_inactivity`. A disabled workflow still exists and its declaration still describes what would run if re-enabled, which is why state is a field and not a filter at collection time.
- `html_url` — the browser URL for the workflow.
- `configuration` — the parsed YAML, and the most important field on this node: triggers, `permissions:`, the job structure, and the raw text at `configuration.raw_yaml` (`req-github-core-workflow-parse`). Un-schema'd today, which is precisely the cost of not yet having `workflow_job` — a blob cannot be queried the way a node can.
- `tags` — TAP's tag map.
