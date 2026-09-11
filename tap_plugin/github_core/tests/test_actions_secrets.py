"""Actions secret NAMES, and what a `${{ secrets.X }}` reference resolves to (github-core#104).

The behaviour under test is almost entirely about **honesty under partial observation**. A secret
listing that could not be read must never render as a scope holding nothing, because the
consequence is specific and bad: a workflow reference that works fine gets reported as naming a
credential that does not exist, and the reader goes looking for a bug that is not there.

Every test here is either "the join is right" or "the absence is honest". Nothing asserts a value,
because there is no value to assert — GitHub's API returns `name`, `created_at`, `updated_at` and
(organisation only) `visibility`, measured 2026-09-10 by executed call.
"""

from __future__ import annotations

from typing import Any

import pytest
import tap_plugin.github_core.models as github  # noqa: F401 — trigger model registration
from tap_plugin.github_core.collectors.github_collector.api_client import GithubAPIError
from tap_plugin.github_core.collectors.github_collector.collector import GithubCollector
from tap_plugin.github_core.collectors.github_collector.identity import (
    account_id,
    actions_secret_id,
    environment_id,
    repository_id,
)
from tap_plugin.github_core.collectors.github_collector.parser import secret_names_in

_OWNER = "acme"
_REPO = "acme/widget"
_DIMS = {
    "github.platform": "github.com",
    "github.owner": _OWNER,
    "github.repo": "widget",
}


class _SecretsClient:
    """Answers the three secret listings from a canned map; anything else is an empty list.

    `statuses` turns one path into a refusal, which is how the not-observed branches are reached.
    """

    #: Mirrors the real client's class-attribute default.
    last_walk_complete = True

    def __init__(
        self,
        rows: dict[str, list[dict[str, Any]]],
        statuses: dict[str, int] | None = None,
        *,
        complete: bool = True,
    ) -> None:
        self.rows = rows
        self.statuses = statuses or {}
        self.calls: list[str] = []
        self.last_walk_complete = complete

    def get_paginated(self, path: str, **_: Any) -> list[Any]:
        self.calls.append(path)
        status = self.statuses.get(path)
        if status is not None:
            raise GithubAPIError(status=status, url=path, body='{"message":"Not Found"}')
        return list(self.rows.get(path, []))


def _collector() -> GithubCollector:
    c = GithubCollector.__new__(GithubCollector)
    c._org_secret_visibility = {}
    c.records: list[tuple[str, str, str]] = []  # type: ignore[attr-defined]
    c.record_warn = lambda site, code, message, **kw: c.records.append(("warn", code, message))  # type: ignore[method-assign]
    c.record_info = lambda site, code, message, **kw: c.records.append(("info", code, message))  # type: ignore[method-assign]
    return c


def _codes(c: GithubCollector, level: str) -> list[str]:
    return [code for lvl, code, _ in c.records if lvl == level]  # type: ignore[attr-defined]


# ---------------------------------------------------------------------------------------------
# The parser: which names a workflow body asks for
# ---------------------------------------------------------------------------------------------


def test_the_parser_finds_referenced_names_and_drops_the_injected_token() -> None:
    """`GITHUB_TOKEN` is the per-run token GitHub injects, not a stored secret.

    It appears in no listing at any scope, so counting it would manufacture an unresolved
    reference on a large share of every estate's workflows for a credential that always exists.
    """
    body = """
    jobs:
      build:
        steps:
          - run: deploy
            env:
              TOKEN: ${{ secrets.GITHUB_TOKEN }}
              KEY: ${{ secrets.DEPLOY_KEY }}
              OTHER: ${{ secrets.deploy_key_lower }}
    """
    assert secret_names_in(body) == {"DEPLOY_KEY", "deploy_key_lower"}


def test_a_body_naming_no_secret_yields_an_empty_set_not_a_failure() -> None:
    assert secret_names_in("jobs:\n  build:\n    steps:\n      - run: echo hi\n") == set()
    assert secret_names_in("") == set()


# ---------------------------------------------------------------------------------------------
# Identity: one credential, one node
# ---------------------------------------------------------------------------------------------


def test_names_differing_only_in_case_are_one_secret() -> None:
    """GitHub secret names are not case-sensitive, so two spellings are one credential.

    Observed on this estate: two of the nine referenced names are written lower-case. A
    case-sensitive key would mint a second node for a secret that already exists and report a
    working reference as broken.
    """
    assert actions_secret_id("repository", _REPO, "harness_pat") == actions_secret_id(
        "repository", _REPO, "HARNESS_PAT"
    )


def test_the_same_name_at_different_scopes_is_different_secrets() -> None:
    """An organisation `AWS_ROLE` and a repository `AWS_ROLE` are different credentials.

    Collapsing them would make an organisation secret appear to live in whichever repository was
    collected last, and would hide a repository override — the case most worth seeing.
    """
    org = actions_secret_id("organization", _OWNER, "AWS_ROLE")
    repo = actions_secret_id("repository", _REPO, "AWS_ROLE")
    env = actions_secret_id("environment", f"{_REPO}/production", "AWS_ROLE")
    assert len({org, repo, env}) == 3


# ---------------------------------------------------------------------------------------------
# Collection: the node, the edge, and no value anywhere
# ---------------------------------------------------------------------------------------------


def test_repository_and_environment_secrets_land_with_their_holders() -> None:
    env_uuid = environment_id(_REPO, "production")
    client = _SecretsClient(
        {
            f"/repos/{_REPO}/actions/secrets": [
                {
                    "name": "DEPLOY_KEY",
                    "created_at": "2026-01-01T00:00:00Z",
                    "updated_at": "2026-02-01T00:00:00Z",
                }
            ],
            f"/repos/{_REPO}/environments/production/secrets": [
                {
                    "name": "PROD_TOKEN",
                    "created_at": "2026-01-01T00:00:00Z",
                    "updated_at": "2026-01-01T00:00:00Z",
                }
            ],
        }
    )
    c = _collector()
    nodes: list[dict[str, Any]] = []
    edges: list[dict[str, Any]] = []
    found, scopes = c._collect_secrets(
        client,
        _REPO,
        repository_id(_REPO),
        _DIMS,
        {"production": env_uuid},
        nodes,
        edges,
    )

    assert scopes == ["repository", "environment"]
    assert set(found) == {"DEPLOY_KEY", "PROD_TOKEN"}
    assert all(len(v) == 1 for v in found.values())
    by_name = {n["node"]["name"]: n["node"] for n in nodes}
    assert by_name["DEPLOY_KEY"]["scope"] == "repository"
    assert by_name["PROD_TOKEN"]["scope"] == "environment"
    assert by_name["PROD_TOKEN"]["environment_name"] == "production"
    # The holder of each is the object the listing belonged to, not the repository for both.
    holders = {(e["edge"]["from_entity_id"], e["edge"]["to_entity_id"]) for e in edges}
    assert (str(repository_id(_REPO)), str(found["DEPLOY_KEY"][0])) in {(str(a), str(b)) for a, b in holders}
    assert (str(env_uuid), str(found["PROD_TOKEN"][0])) in {(str(a), str(b)) for a, b in holders}


def test_no_field_on_a_secret_node_can_carry_a_value() -> None:
    """The node's fields are the listing's fields plus scope. There is nowhere for a value to go.

    Asserted rather than assumed: `configuration` is the obvious place a future change would park
    an unexamined response blob, and this is the test that would fail when it did.
    """
    client = _SecretsClient(
        {f"/repos/{_REPO}/actions/secrets": [{"name": "DEPLOY_KEY", "value": "s3cr3t-should-be-ignored"}]}
    )
    c = _collector()
    nodes: list[dict[str, Any]] = []
    c._collect_secrets(client, _REPO, repository_id(_REPO), _DIMS, {}, nodes, [])

    node = nodes[0]["node"]
    assert node["configuration"] == {}
    assert "value" not in node
    assert "s3cr3t-should-be-ignored" not in str(node)


def test_an_organisation_secret_carries_its_sharing_visibility() -> None:
    """`visibility` is the blast-radius field: how many repositories one credential reaches."""
    client = _SecretsClient({f"/orgs/{_OWNER}/actions/secrets": [{"name": "OPENAI_API_KEY", "visibility": "all"}]})
    c = _collector()
    nodes: list[dict[str, Any]] = []
    edges: list[dict[str, Any]] = []
    found, observed = c._collect_org_secrets(client, _OWNER, nodes, edges)

    assert observed is True
    assert set(found) == {"OPENAI_API_KEY"}
    assert nodes[0]["node"]["visibility"] == "all"
    assert nodes[0]["node"]["scope"] == "organization"
    assert nodes[0]["node"]["full_name"] == "", "an organisation secret belongs to no repository"
    assert str(edges[0]["edge"]["from_entity_id"]) == str(account_id(_OWNER))


# ---------------------------------------------------------------------------------------------
# Honest absence: none / some / NOT OBSERVABLE
# ---------------------------------------------------------------------------------------------


@pytest.mark.parametrize("status", [401, 403, 404])
def test_a_refused_repository_listing_is_not_a_repository_without_secrets(
    status: int,
) -> None:
    """The scope drops out of `scopes_read` AND a warning is recorded.

    An empty map alone would be indistinguishable from a repository that genuinely holds none —
    the most reassuring possible reading of a permission failure.
    """
    client = _SecretsClient({}, statuses={f"/repos/{_REPO}/actions/secrets": status})
    c = _collector()
    found, scopes = c._collect_secrets(client, _REPO, repository_id(_REPO), _DIMS, {}, [], [])

    assert found == {}
    assert scopes == [], "a scope that refused was not read"
    assert any(code.startswith("SECRETS_UNREADABLE") for code in _codes(c, "warn"))


def test_a_refused_environment_listing_drops_environment_scope_only() -> None:
    """One unreadable environment must not cost the repository scope its observed status."""
    client = _SecretsClient(
        {f"/repos/{_REPO}/actions/secrets": [{"name": "DEPLOY_KEY"}]},
        statuses={f"/repos/{_REPO}/environments/production/secrets": 403},
    )
    c = _collector()
    found, scopes = c._collect_secrets(
        client,
        _REPO,
        repository_id(_REPO),
        _DIMS,
        {"production": environment_id(_REPO, "production")},
        [],
        [],
    )

    assert set(found) == {"DEPLOY_KEY"}
    assert scopes == ["repository"]
    assert any(code.startswith("ENVIRONMENT_SECRETS_UNREADABLE") for code in _codes(c, "warn"))


def test_a_repository_with_no_environments_never_claims_environment_scope() -> None:
    """Nothing was asked, so nothing may be claimed — `[]` of environments is not a read scope."""
    client = _SecretsClient({f"/repos/{_REPO}/actions/secrets": []})
    c = _collector()
    _, scopes = c._collect_secrets(client, _REPO, repository_id(_REPO), _DIMS, {}, [], [])
    assert scopes == ["repository"]


def test_a_user_account_404_is_information_not_a_degradation() -> None:
    """A user account has no organisation-secrets endpoint at all.

    That is *nothing to ask*, not *refused*, and recording it as a warning would put a permanent
    false alarm on every single-user deployment.
    """
    client = _SecretsClient({}, statuses={f"/orgs/{_OWNER}/actions/secrets": 404})
    c = _collector()
    found, observed = c._collect_org_secrets(client, _OWNER, [], [])

    assert found == {}
    assert observed is False, "no organisation scope was read, whatever the reason"
    assert _codes(c, "warn") == []
    assert "ORG_SECRETS_NOT_AN_ORGANISATION" in _codes(c, "info")


def test_a_repos_only_scope_names_no_account_to_ask() -> None:
    c = _collector()
    found, observed = c._collect_org_secrets(_SecretsClient({}), None, [], [])
    assert (found, observed) == ({}, False)
    assert "ORG_SECRETS_SKIPPED" in _codes(c, "info")


def test_a_listing_that_stopped_at_the_page_cap_is_reported_as_a_prefix() -> None:
    """An incomplete enumeration is a prefix of the names, not the set of them."""
    client = _SecretsClient({f"/repos/{_REPO}/actions/secrets": [{"name": "A"}]})
    client.last_walk_complete = False
    c = _collector()
    c._collect_secrets(client, _REPO, repository_id(_REPO), _DIMS, {}, [], [])
    assert "SECRET_LISTING_INCOMPLETE" in _codes(c, "warn")


def test_a_non_permission_error_still_raises() -> None:
    """A 500 is not a permission fact and must not be swallowed into 'not observed'."""
    client = _SecretsClient({}, statuses={f"/repos/{_REPO}/actions/secrets": 500})
    with pytest.raises(GithubAPIError):
        _collector()._collect_secrets(client, _REPO, repository_id(_REPO), _DIMS, {}, [], [])


# ---------------------------------------------------------------------------------------------
# Regressions found by the unified review on PR# 105. Both sat in the honesty logic, which is the
# only thing this surface exists for.
# ---------------------------------------------------------------------------------------------


def test_a_truncated_listing_does_not_count_as_a_read_scope() -> None:
    """The failure that hides: a walk stopping at the page cap returns plausible names.

    The scope was then listed in `scopes_read`, so a secret sitting on an unfetched page made
    every workflow naming it report `unresolved` *under a scope the tag claimed was read* — the
    reassuring direction, reached from a listing that looked like it had worked.
    """
    client = _SecretsClient({f"/repos/{_REPO}/actions/secrets": [{"name": "PAGE_ONE"}]}, complete=False)
    c = _collector()
    found, scopes = c._collect_secrets(client, _REPO, repository_id(_REPO), _DIMS, {}, [], [])

    assert set(found) == {"PAGE_ONE"}, "the names we DID see are real and still resolve"
    assert scopes == [], "but the scope was not enumerated, so it is not a read scope"
    assert "SECRET_LISTING_INCOMPLETE" in _codes(c, "warn")


def test_one_truncated_environment_costs_the_environment_scope_its_claim() -> None:
    """A reference resolves against the repository's environments as a SET.

    Lose one and we no longer hold the set, so the scope cannot be claimed even though the other
    environment answered in full.
    """
    envs = {
        "production": environment_id(_REPO, "production"),
        "staging": environment_id(_REPO, "staging"),
    }

    class _PartialClient(_SecretsClient):
        def get_paginated(self, path: str, **kw: Any) -> list[Any]:
            rows = super().get_paginated(path, **kw)
            self.last_walk_complete = "staging" not in path
            return rows

    client = _PartialClient(
        {
            f"/repos/{_REPO}/actions/secrets": [],
            f"/repos/{_REPO}/environments/production/secrets": [{"name": "PROD"}],
            f"/repos/{_REPO}/environments/staging/secrets": [{"name": "STAGE"}],
        }
    )
    c = _collector()
    found, scopes = c._collect_secrets(client, _REPO, repository_id(_REPO), _DIMS, envs, [], [])

    assert set(found) == {"PROD", "STAGE"}
    assert "environment" not in scopes
    assert "repository" in scopes, "the repository listing was complete and keeps its claim"


def test_one_name_at_two_scopes_keeps_both_credentials() -> None:
    """`AWS_ROLE` held by the organisation AND by the repository is two credentials.

    The first cut wrote both into one slot, so the map carried whichever was collected last and
    a workflow naming it got a single edge — attribution decided by dict iteration order.
    """
    org_client = _SecretsClient({f"/orgs/{_OWNER}/actions/secrets": [{"name": "AWS_ROLE", "visibility": "all"}]})
    repo_client = _SecretsClient({f"/repos/{_REPO}/actions/secrets": [{"name": "AWS_ROLE"}]})
    c = _collector()
    org_found, _ = c._collect_org_secrets(org_client, _OWNER, [], [])
    repo_found, _ = c._collect_secrets(repo_client, _REPO, repository_id(_REPO), _DIMS, {}, [], [])

    merged: dict[str, list[Any]] = {k: list(v) for k, v in org_found.items()}
    for k, v in repo_found.items():
        merged.setdefault(k, []).extend(v)

    assert len(merged["AWS_ROLE"]) == 2, "both defining scopes survive the merge"
    assert merged["AWS_ROLE"][0] == actions_secret_id("organization", _OWNER, "AWS_ROLE")
    assert merged["AWS_ROLE"][1] == actions_secret_id("repository", _REPO, "AWS_ROLE")


def test_a_selected_visibility_org_secret_is_not_claimed_to_reach_this_repository() -> None:
    """`visibility: selected` names a repository list this collector does not fetch.

    So a name resolved ONLY by such a secret is neither resolved nor unresolved for this
    repository, and gets its own answer rather than being rounded to the reassuring one.
    """
    client = _SecretsClient(
        {f"/orgs/{_OWNER}/actions/secrets": [{"name": "SHARED", "visibility": "selected"}]}
    )
    c = _collector()
    found, observed = c._collect_org_secrets(client, _OWNER, [], [])

    assert observed is True
    assert set(found) == {"SHARED"}
    assert c._org_secret_visibility["SHARED"] == "selected"
