"""Deterministic UUIDv5 entity IDs for github_core nodes.

Spec: plugins/github_core/specs/spec-github-core-v0.md
(req-github-core-models-4 Deterministic Identity). The natural-key table in
the spec is the source of truth for each entity type's identity inputs.
"""

from __future__ import annotations

from uuid import NAMESPACE_DNS, UUID, uuid5

# A stable, github_core-specific namespace derived from the canonical DNS
# namespace. Using a fixed UUID here keeps entity IDs reproducible across
# environments without depending on a runtime random seed.
GITHUB_CORE_NAMESPACE: UUID = uuid5(NAMESPACE_DNS, "github_core.tap")


def _id(entity_type: str, natural_key: str) -> UUID:
    return uuid5(GITHUB_CORE_NAMESPACE, f"{entity_type}:{natural_key}")


def platform_id(host: str) -> UUID:
    # Natural key is the host ("github.com"); a GHES tenant gets its own id.
    return _id("github_core__github_platform", host)


def account_id(login: str) -> UUID:
    return _id("github_core__github_account", login)


def repository_id(full_name: str) -> UUID:
    return _id("github_core__github_repository", full_name)


def workflow_id(full_name: str, workflow_id_int: int | str) -> UUID:
    return _id("github_core__github_workflow", f"{full_name}#{workflow_id_int}")


def github_app_id(slug: str) -> UUID:
    # Natural key is the app slug ("dependabot"); one app node is shared across
    # every repo that enables it (ENABLED_ON edges fan in).
    return _id("github_core__github_app", slug)


def workflow_job_id(full_name: str, workflow_id_int: int | str, job_key: str) -> UUID:
    """A DECLARED job: the workflow it is written in, plus its YAML key.

    Keyed on the workflow id rather than the file path so a renamed file keeps the same job
    nodes, and on the job key rather than the display name because `name:` is free text an
    author changes without changing what the job is.
    """
    return _id("github_core__workflow_job", f"{full_name}#{workflow_id_int}#{job_key}")


def git_ref_id(full_name: str, ref: str) -> UUID:
    """A ref, keyed on its FULL path (`refs/heads/main`).

    The full path rather than the short name, because a branch and a tag may share one
    (`refs/heads/release` and `refs/tags/release` are different objects with the same name).
    """
    return _id("github_core__git_ref", f"{full_name}#{ref}")


def ruleset_id(owner: str, ruleset_id_int: int | str) -> UUID:
    """A ruleset, keyed on owner + GitHub's ruleset id.

    Not repo-scoped: one organization ruleset applies to many repositories and must be ONE node
    that many repositories point at, or the question "what does this ruleset protect" becomes a
    string comparison across duplicates. Measured on the fixture org: 3 organization rulesets
    reported by 19 repositories = 57 attachments over 3 nodes.

    The owner prefix is belt-and-braces. GitHub's ruleset `databaseId` was measured to be
    PLATFORM-global rather than per-account — org- and repo-sourced ids interleave when
    sorted, id order is exactly creation order, and an org owning six rulesets holds ids near
    20.6 million rather than 1-6 — so the bare id would also have keyed correctly. Recorded so
    nobody re-derives it: the prefix costs nothing and a natural key cannot be changed once
    nodes exist.
    """
    return _id("github_core__github_ruleset", f"{owner}#{ruleset_id_int}")


def rule_suite_id(suite_id_int: int | str) -> UUID:
    """A rule suite, keyed on GitHub's own suite id — unique across the platform.

    Not scoped by repository: the id is assigned by GitHub and the suite carries its own
    `repository_name`, so scoping would add nothing and would break the join if the same
    suite were ever reached from another path.
    """
    return _id("github_core__rule_suite", str(suite_id_int))


def environment_id(full_name: str, name: str) -> UUID:
    return _id("github_core__github_environment", f"{full_name}#{name}")


def actions_cache_id(full_name: str, cache_id_int: int | str) -> UUID:
    return _id("github_core__actions_cache", f"{full_name}#{cache_id_int}")


def app_installation_id(installation_id_int: int | str) -> UUID:
    """An installation, keyed on GitHub's installation id — unique across the platform."""
    return _id("github_core__app_installation", str(installation_id_int))


def run_id(full_name: str, run_id_int: int | str) -> UUID:
    # v0 natural key is owner/repo + run_id (run_attempt deferred — see
    # req-github-core-backlog-run-attempts).
    return _id("github_core__github_actions_run", f"{full_name}#{run_id_int}")


def job_id(full_name: str, job_id_int: int | str) -> UUID:
    return _id("github_core__github_actions_job", f"{full_name}#{job_id_int}")


def runner_id(full_name: str, runner_id_int: int | str) -> UUID:
    return _id("github_core__github_runner", f"{full_name}#{runner_id_int}")


def release_id(full_name: str, release_id_int: int | str) -> UUID:
    """A release, keyed on `owner/repo` + GitHub's release id (github-core#31).

    The id rather than the tag name: a release can be deleted and re-cut on the same tag, and
    a tag can be moved under a release, and both must read as what they are — a different
    object, or the same object whose `target_sha` changed — rather than being folded together.
    """
    return _id("github_core__github_release", f"{full_name}#{release_id_int}")


def actions_artifact_id(full_name: str, artifact_id_int: int | str) -> UUID:
    return _id("github_core__actions_artifact", f"{full_name}#{artifact_id_int}")


def package_id(owner: str, package_type: str, name: str) -> UUID:
    """A package, keyed on owner + type + name — GitHub's own path to it.

    Not on the numeric id: the REST path `/orgs/{owner}/packages/{type}/{name}` is how every
    later surface reaches the package, and a deleted-and-republished package of the same name
    IS the same thing to every consumer that pulls it by name.
    """
    return _id("github_core__github_package", f"{owner}#{package_type}#{name}")


def package_version_id(owner: str, package_type: str, name: str, version_id_int: int | str) -> UUID:
    """A version, scoped under its package and keyed on GitHub's version id.

    GitHub's id rather than the version name: for a container the name is a digest, which is
    content-addressed and would key correctly, but for npm/maven a version string can be
    unpublished and re-published as different bytes, and the id is what tells them apart.
    """
    return _id("github_core__github_package_version", f"{owner}#{package_type}#{name}#{version_id_int}")


#: GitHub Packages registry host per package type — the `repository_url` a purl needs to say
#: that this npm package lives on GitHub's registry rather than npmjs.org.
_REGISTRY_HOST_BY_TYPE = {
    "npm": "npm.pkg.github.com",
    "maven": "maven.pkg.github.com",
    "rubygems": "rubygems.pkg.github.com",
    "nuget": "nuget.pkg.github.com",
}
#: purl type per GitHub package type where the purl spec has one of its own.
_PURL_TYPE_BY_TYPE = {"npm": "npm", "maven": "maven", "rubygems": "gem", "nuget": "nuget"}


def package_purl(package_type: str, owner: str, name: str, version: str = "") -> str:
    """Package-URL for a GitHub Packages package, per the purl spec's type registry.

    The vocabulary corpus (decision 4) keys `package` / `package_version` on a purl and homes them
    in a future `supply_chain_core`. This is the seam: github_core mints the purl from what the
    GitHub surface knows, so the substrate can claim these nodes by identity later.

    * `container` (ghcr.io) -> `pkg:docker/ghcr.io/<owner>/<name>@<digest>` — the purl spec's
      docker type with the registry in the namespace, as its own examples do for gcr.io.
    * `docker` (the retired docker.pkg.github.com registry) -> `pkg:docker/docker.pkg.github.com/...`.
    * npm / maven / rubygems / nuget -> that ecosystem's purl type with
      `?repository_url=<host>.pkg.github.com`, because the bare purl would name the public registry.
    * anything else -> `pkg:github/<owner>/<name>@<version>`, the spec's GitHub-hosted type.

    Owner and name are lowercased for the docker forms only — OCI references are case-sensitive
    and always lowercase, and GitHub lowercases them on push. Every other form keeps the case
    GitHub returned.
    """
    ptype = package_type.lower()
    at = f"@{version}" if version else ""
    if ptype == "container":
        return f"pkg:docker/ghcr.io/{owner.lower()}/{name.lower()}{at}"
    if ptype == "docker":
        return f"pkg:docker/docker.pkg.github.com/{owner.lower()}/{name.lower()}{at}"
    purl_type = _PURL_TYPE_BY_TYPE.get(ptype)
    if purl_type is not None:
        host = _REGISTRY_HOST_BY_TYPE[ptype]
        if ptype == "npm":
            # GitHub-hosted npm packages are always scoped by owner: `@owner/name`. The purl spec
            # percent-encodes the `@` of a scope in the namespace.
            return f"pkg:npm/%40{owner}/{name}{at}?repository_url={host}"
        if ptype == "maven":
            # GitHub reports a Maven package as `group.artifact` in one string; the last dotted
            # segment is the artifact and the rest the group, which is the spec's namespace.
            group, _, artifact = name.rpartition(".")
            return f"pkg:maven/{group or owner}/{artifact}{at}?repository_url={host}"
        return f"pkg:{purl_type}/{name}{at}?repository_url={host}"
    return f"pkg:github/{owner}/{name}{at}"


def edge_id(edge_type: str, source: UUID, target: UUID) -> UUID:
    """Deterministic UUIDv5 for an edge by (type, source, target)."""
    return uuid5(GITHUB_CORE_NAMESPACE, f"edge:{edge_type}:{source}:{target}")
