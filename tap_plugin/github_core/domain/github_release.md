# github_release

## Blurb

A published release — the tag it was cut on, the commit that tag resolved to when observed, who published it, and the assets attached. The first node in the machinery view's OUTPUT column (github-core#31), which rendered "not yet collected" until this landed.

## Purpose

The vocabulary corpus names `github_release` on six sources at the self-lite tier, and the product repository cuts one on every merge that release-please decides is a version. Nothing on the grid recorded that. A pipeline view with no outputs is a view of machinery with nothing coming out the end — every "what did this run actually produce" question was unanswerable, and the column that should answer it had to say so with a placeholder.

A release is also the anchor for the tag-movement detections the corpus flags on three incidents: the release records the commit its tag pointed at *when the release was observed*, the tag ref records what it points at *now*, and a difference is a re-tag.

## Goals

1. Put the released outputs on the grid, keyed so a re-cut release and a moved tag each read as what they are.
2. Join each release to the tag it was cut on and — as honestly as GitHub's data allows — to the run that produced it.
3. Retire the "not yet collected" placeholder for releases with an observed answer, and keep it for the cases where the answer was not observed.

## Identity

Natural key: `owner/repo` + GitHub's release **id** (`databaseId`). Entity id is `uuid5(ns, "github_core__github_release:<full_name>#<id>")`.

The id rather than the tag name, and the difference is the point. A release can be deleted and re-cut on the same tag; a tag can be moved under a release. Keyed on the tag, the first collapses two objects into one and the second is invisible. Keyed on the id, a re-cut is a new node and a re-tag is the same node whose `target_sha` changed — which the grid's field history records without the collector doing anything. The issue that pulled this type asked for the id explicitly, and this is why it was right to.

Repo-scoped because GitHub's release ids are assigned per platform but reached per repository, and a release moved with a transferred repository keeps its id under the new name — which is the same continuity `github_repository.github_id` preserves.

## Boundaries

- **Not a tag.** The tag is a `git_ref`; the release is a GitHub object attached to it. `TARGETS_REF` joins them.
- **Not a package or an artifact.** Assets attached to a release are files on the release (`assets`), not `actions_artifact` nodes (uploaded by a run, retained for a window) and not `github_package_version` nodes (in a registry). Three outputs, three types, because the three have different producers, different retention and different consumers.
- **Not an attestation.** Whether the release's assets are signed or provenance-attested is a `sigstore_core` question. The corpus's `ATTESTED_BY` is not built here.
- **Not a change record.** A release edited after publication is field history on this node.

## Neutrality

**Neutral-capable, vendor-collected.** The corpus marks `github_release` neutral: a GitLab release, a Gitea release and a kernel tarball announcement all populate the concept. The slug carries the vendor prefix because that is what every other self-tier slug does, and the fields (`is_draft`, `is_latest`) are GitHub's own. When the neutral substrate is extracted this is a candidate to travel; until then the prefix is honest about where the data came from.

## Observability

Populated from the config-layer GraphQL query (`repository.releases`, newest first, `first: 50`, with `releaseAssets(first: 50)`) at **`repository:contents:read`**, which was already in the derived permission union — so this surface widened nothing. Measured 2026-09-02 against `unified-systems-com/tap` with a read-only App installation token: five releases, no `errors` entry, and the whole config-layer query moved from 64 to 66 rate-limit points.

**A release is a product of execution riding the declaration transport.** It arrives in the same response as rulesets and refs, and it is stamped `github.observation: execution` regardless, because it exists only because someone published it. Transport is not layer.

**Three states, on the repository node.** `github_repository.outputs_observability.releases` is `observed` when the field answered, and `unobservable` — with the reason in `notes.releases` — when a repos-only scope ran no config-layer query or GraphQL degraded the field. A view that reads only release nodes cannot tell "no releases" from "did not look"; the repository node can.

**Truncation is reported.** `totalCount` is selected, and when the cap leaves releases behind the run warns with the count and states that absence in the batch is not evidence of deletion.

**Not observable:** which run created the release. GitHub records the *author* (an account — `tap-release-please[bot]` on the product repository) but not the workflow run, so `BUILDS_RELEASE` is derived and labels its own derivation. Also not observable here: release notes bodies (not selected — they can be large and nothing points at them), and whether a draft was later published (field history).

## Authoritative Source

- **Source:** GitHub GraphQL API — `Repository.releases` (`Release`, `ReleaseAsset`), selected inside the config-layer query
- **Version:** GraphQL schema as pinned in `github_openapi_extract.json` (`graphql.commit`); REST API version `2022-11-28` for the equivalent `GET /repos/{owner}/{repo}/releases`, which the collector does not call
- **Retrieved:** 2026-09-02 (the connection captured live and committed as `tests/fixtures/outputs.json`)

## Prior Art

- `specs/spec-github-core-vocabulary.md` (2026-08-27) — `github_release`, self-lite, six sources; neutral.
- `git-serious-tap/docs/doc-git-serious-cicd-shape-review.md` §4.3.6 (2026-08-27) — the earlier shape proposal (keyed on tag name, `PUBLISHES_RELEASE`, derived `BUILDS_RELEASE`). The edge names were kept; the key was changed to the id for the reasons under Identity.
- `git-serious-tap/docs/doc-git-serious-vocab-from-incidents.md` rows 11 and 29 (2026-08-27) — the incidents that require a release object (SolarWinds: the release existed, the published artifact was never joined to a build).
- GitHub GraphQL API reference, `Release` object (read 2026-09-02) — `tagCommit` is documented as "the commit the release's tag points to", which is the field tag-movement detection needs.

## Fields

- `release_id` — GitHub's release id and, with `full_name`, the natural key. Nullable only because the grid's create contract wants every field declared.
- `full_name` — `owner/repo`, the other half of the key and how a release is attributable without walking edges.
- `tag_name` — the tag the release was cut on, without the `refs/tags/` prefix, as GitHub names it. The join onto `git_ref` is `refs/tags/<tag_name>`.
- `name` — the release title. Free text; often equal to the tag.
- `is_draft` / `is_prerelease` / `is_latest` — GitHub's three flags, nullable so a degraded read stores "not observed" rather than `false`, which would be a claim that the release is final.
- `author_login` — the account that published it. An account, never an identity: a bot login lands verbatim and nothing here says whether it is a person.
- `target_sha` — the commit the tag resolved to **when observed**. Compared with the tag ref's `head_sha` later, a difference is a tag moved under a published release — the detection three incidents in the corpus turn on.
- `created_at` / `published_at` — GitHub's timestamps; a draft has the first and not the second. Null is unobserved, never "now".
- `html_url` — the release page.
- `asset_count` — GitHub's `totalCount` of assets, kept separately from `len(assets)` so a capped asset list reads as capped.
- `assets` — `[{"name", "size", "content_type", "download_url", "created_at"}]`, the files attached. Data rather than nodes because nothing points at an asset yet; when an attestation surface does, that is the moment to promote them.
- `configuration` — JSONB residue for what the API returns that is not lifted into a column.
- `tags` — TAP's own tag map, uniform across every model.
