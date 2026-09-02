# actions_artifact

## Blurb

A file a workflow run uploaded and GitHub is holding — its name, size, content digest, expiry, and the run and ref that produced it. The observed upload; the declared `actions/upload-artifact` step stays on the job.

## Purpose

Eleven sources in the vocabulary corpus name the artifact, and one incident is made of nothing else: ArtiPACKED, where a job uploaded its checkout directory with `persist-credentials` still true and the artifact carried a live token to anyone who could download it. The grid could not represent that shape — there was no artifact to point at — and the machinery view's output column said so with a placeholder.

An artifact is also the one output GitHub attributes to its producer directly. A release records an author; a package version records nothing; an artifact records the `workflow_run` that uploaded it. That makes `UPLOADS_ARTIFACT` the only reported (not derived) producer edge in the output column, and worth building for that alone.

## Goals

1. Land the uploaded artifacts with GitHub's own attribution to the run that made them.
2. Carry the ref the producing run was on, because "uploaded from a fork's pull request, downloaded by a privileged workflow" is the shape the incident has.
3. Say how many artifacts the cap left behind — on an active repository that is nearly all of them.

## Identity

Natural key: `owner/repo` + GitHub's artifact **id**. Entity id is `uuid5(ns, "github_core__actions_artifact:<full_name>#<id>")`.

Not the name: a workflow uploads an artifact of the same name on every run, and each is a different file with a different digest. Not the digest: two runs can upload byte-identical artifacts and they are still two uploads with two expiries. The id is the only thing GitHub promises is one-per-upload.

## Boundaries

- **Not the declared step.** Which job *declares* an upload (`uses: actions/upload-artifact` with its `path:`) is `workflow_job` data. The corpus's `UPLOADS_ARTIFACT` is sourced there; this plugin sources it on the run, and the article for that edge says why. The declared side is open.
- **Not a download.** `DOWNLOADS_ARTIFACT` is not built: GitHub exposes no download log, and the declared `actions/download-artifact` steps would need the step parser first.
- **Not a cache.** `actions_cache` is keyed content restored by key; an artifact is a named file retained for a window. Both are by-products of a run; they are different by-products.
- **Not the content.** Nothing here fetches the archive.

## Neutrality

**Neutral-capable.** GitLab CI has job artifacts of exactly this shape; the kernel pressure test has no analogue. The corpus's reading — neutral-capable, not neutral-proven — stands, and the slug carries no `github_` prefix, as `actions_cache` does not, because "Actions" is already the product name.

## Observability

Populated from `GET /repos/{owner}/{repo}/actions/artifacts?per_page=100` at **`repository:actions:read`**, already in the derived union — nothing widened. **REST-only: GraphQL exposes no artifacts**, as it exposes no runs or jobs.

Measured 2026-09-02 against `unified-systems-com/tap` with a read-only App installation token: **200, `total_count: 3636`**, each item carrying `digest`, `expired`, `expires_at` and a `workflow_run` object with the run id, `head_sha` and `head_branch`. The endpoint is marked `enabledForGitHubApps: true` in GitHub's OpenAPI description, and behaved that way.

**The cap is the rule, not the exception.** One page of one hundred, newest first, against thousands. The run warns with GitHub's `total_count` and states that absence in the batch is not evidence an artifact expired or was deleted. A per-run listing (`/actions/runs/{id}/artifacts`) would be complete for the runs in the window at one extra call per run; that is the single largest cost in the collection already, and is left for demand.

**Three states, on the repository node.** `outputs_observability.artifacts` is `observed` on a 200 and `unobservable` on a 403 or 404, with the status in `notes.artifacts`. A refusal lands no nodes and is recorded, never rendered as a repository that uploads nothing.

**Not observable:** who downloaded an artifact, and whether it was downloaded at all. Also not observable from this listing: the `path:` that was uploaded — that is on the declared step.

## Authoritative Source

- **Source:** GitHub REST API — Actions artifacts (`GET /repos/{owner}/{repo}/actions/artifacts`)
- **Version:** REST API version `2022-11-28`; OpenAPI description pinned in `github_openapi_extract.json` (`spec_commit`)
- **Retrieved:** 2026-09-02 (three items and the total captured live into `tests/fixtures/outputs.json`)

## Prior Art

- `specs/spec-github-core-vocabulary.md` (2026-08-27) — `actions_artifact`, friends tier, eleven sources; `UPLOADS_ARTIFACT` / `DOWNLOADS_ARTIFACT` with `{cross_workflow}`.
- `git-serious-tap/docs/doc-git-serious-vocab-from-incidents.md` §5.9 and row 14 (2026-08-27) — ArtiPACKED, and observable condition 7 (`persist-credentials: false`; no artifact upload of the checkout dir).
- GitHub REST API reference, *List artifacts for a repository* (read 2026-09-02) — `workflow_run` on the artifact object is the attribution this type relies on.

## Fields

- `artifact_id` — GitHub's artifact id; with `full_name`, the natural key.
- `full_name` — `owner/repo`, so an artifact is attributable without walking edges.
- `name` — the name the uploading step gave it. Repeats across runs; not identity.
- `size_in_bytes` — as reported.
- `digest` — `sha256:<hex>` of the content as GitHub reports it. Empty when GitHub did not return one (older artifacts), never fabricated.
- `expired` — GitHub's flag; nullable so a degraded read does not claim the file is still there.
- `run_id` — the run that uploaded it, GitHub's own attribution. Kept as a field as well as the `UPLOADS_ARTIFACT` edge, because the edge is emitted only when that run is in the collected window and the field is always true.
- `head_sha` — the commit the producing run built.
- `head_branch` — the ref the producing run was on, as GitHub reports it: a bare branch name, so a pull-request artifact reads as its source branch rather than `refs/pull/N/merge`. The trust-boundary field.
- `created_at` / `updated_at` / `expires_at` — GitHub's timestamps. Null is unobserved.
- `archive_download_url` — the API URL of the zip. A URL, not the content.
- `configuration` — JSONB residue for what the API returns that is not lifted into a column.
- `tags` — TAP's own tag map.
