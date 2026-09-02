"""Unit tests for the github_core collector building blocks.

Spec: plugins/github_core/specs/spec-github-core-v0.md
"""

from __future__ import annotations

import jsonschema
import pytest
from tap_plugin.github_core.collectors.github_collector.identity import (
    account_id,
    edge_id,
    job_id,
    repository_id,
    run_id,
    runner_id,
    workflow_id,
)
from tap_plugin.github_core.collectors.github_collector.manifest import (
    load_collection_manifest,
    load_link_manifest,
)
from tap_plugin.github_core.collectors.github_collector.parser import parse_workflow_yaml
from tap_plugin.github_core.collectors.github_collector.secret import (
    GITHUB_PAT_SCHEMA,
    api_base_url,
    initial_run_limit,
)


class TestManifests:
    def test_collection_manifest_loads(self) -> None:
        manifest = load_collection_manifest()
        assert manifest["manifest_version"] == "0"
        assert {s["name"] for s in manifest["sources"]} >= {
            "account",
            "repository",
            "workflows",
            "workflow_yaml",
            "runs",
            "jobs",
            "runners",
        }

    def test_link_manifest_loads(self) -> None:
        manifest = load_link_manifest()
        assert manifest["manifest_version"] == "0"
        edge_types = {r["edge_type"] for r in manifest["rules"]}
        # YAML-ref rules emit REFERENCES_RESOURCE; the structural OIDC rule emits
        # FEDERATES_VIA (repo -> aws_iam_oidc_provider); the issuer-convergence
        # rule emits TRUSTS_ISSUER (aws_iam_oidc_provider -> identity_core__oidc_issuer),
        # the generic identity_core-owned edge type this github enrichment rule emits.
        assert edge_types == {
            "REFERENCES_RESOURCE__github_core",
            "FEDERATES_VIA__github_core",
            "TRUSTS_ISSUER__identity_core",
        }

    def test_link_manifest_oneof_source_enforced(self) -> None:
        """Schema must reject rules with both source_field_path and source_constant."""
        import jsonschema
        from tap_plugin.github_core.collectors.github_collector.manifest import (
            LINK_MANIFEST_SCHEMA_PATH,
        )

        schema = __import__("json").loads(LINK_MANIFEST_SCHEMA_PATH.read_text())
        bad = {
            "manifest_version": "0",
            "rules": [
                {
                    "name": "bad",
                    "source_entity_type": "github_core__github_repository",
                    "source_field_path": "x.y",
                    "source_constant": "z",
                    "target_entity_type": "aws_core__aws_iam_oidc_provider",
                    "target_field": "url",
                    "edge_type": "REFERENCES_RESOURCE__github_core",
                    "match_mode": "exact",
                }
            ],
        }
        with pytest.raises(jsonschema.ValidationError):
            jsonschema.validate(bad, schema)

    def test_link_manifest_includes_oidc_rule(self) -> None:
        manifest = load_link_manifest()
        oidc = [r for r in manifest["rules"] if r.get("target_entity_type") == "aws_core__aws_iam_oidc_provider"]
        assert len(oidc) == 1
        rule = oidc[0]
        assert rule["source_constant"] == "token.actions.githubusercontent.com"
        assert rule["near_match_pattern"] == r"(?i)githubusercontent\.com"
        # The federation rule emits the dedicated FEDERATES_VIA edge (not the
        # generic REFERENCES_RESOURCE) — repo -> aws_iam_oidc_provider.
        assert rule["edge_type"] == "FEDERATES_VIA__github_core"


class TestPATSchema:
    def test_minimal_valid_pat(self) -> None:
        jsonschema.validate(
            {"token": "ghp_x", "repos": ["notgeorge/samsite"]},
            GITHUB_PAT_SCHEMA,
        )

    def test_pat_with_optional_fields(self) -> None:
        jsonschema.validate(
            {
                "token": "ghp_x",
                "api_base_url": "https://github.enterprise.example/api/v3",
                "repos": ["notgeorge/samsite", "notgeorge/another"],
                "initial_run_limit": 25,
            },
            GITHUB_PAT_SCHEMA,
        )

    def test_pat_missing_token_rejected(self) -> None:
        with pytest.raises(jsonschema.ValidationError):
            jsonschema.validate({"repos": ["notgeorge/samsite"]}, GITHUB_PAT_SCHEMA)

    def test_pat_owner_only_valid(self) -> None:
        """req-github-core-org-scope: an account scope needs no repo list."""
        jsonschema.validate({"token": "ghp_x", "owner": "unified-systems-com"}, GITHUB_PAT_SCHEMA)

    def test_pat_owner_with_repos_filter_valid(self) -> None:
        jsonschema.validate(
            {"token": "ghp_x", "owner": "unified-systems-com", "repos": ["unified-systems-com/tap"]},
            GITHUB_PAT_SCHEMA,
        )

    def test_pat_neither_owner_nor_repos_rejected(self) -> None:
        with pytest.raises(jsonschema.ValidationError):
            jsonschema.validate({"token": "ghp_x"}, GITHUB_PAT_SCHEMA)

    def test_pat_malformed_owner_rejected(self) -> None:
        for bad in ("", "-leading", "has/slash", "trailing-"):
            with pytest.raises(jsonschema.ValidationError):
                jsonschema.validate({"token": "ghp_x", "owner": bad}, GITHUB_PAT_SCHEMA)

    def test_pat_api_base_url_must_be_https_and_bare(self) -> None:
        """The value is interpolated into every request URL and handed to `urlopen`, which
        honours whatever scheme it is given. Constrained at the schema — where the value enters
        the system — rather than at each of the two clients that consume it."""
        for good in ("https://api.github.com", "https://ghe.example.com/api/v3"):
            jsonschema.validate({"token": "ghp_x", "owner": "acme", "api_base_url": good}, GITHUB_PAT_SCHEMA)
        for bad in (
            "http://api.github.com",       # PAT would travel in cleartext
            "file:///etc/passwd",          # urlopen reads local files
            "https://u:p@evil.example",    # credentials in the URL
            "https://api.github.com?x=1",  # query smuggling
            "api.github.com",              # no scheme
            "https://",                    # no host
        ):
            with pytest.raises(jsonschema.ValidationError):
                jsonschema.validate(
                    {"token": "ghp_x", "owner": "acme", "api_base_url": bad}, GITHUB_PAT_SCHEMA
                )

    def test_every_schema_field_is_described(self) -> None:
        """House rule: JSON structures carry descriptions — top level and every property."""
        assert GITHUB_PAT_SCHEMA["description"]
        for name, prop in GITHUB_PAT_SCHEMA["properties"].items():
            assert prop.get("description"), f"{name} has no description"

    def test_pat_empty_repos_rejected(self) -> None:
        with pytest.raises(jsonschema.ValidationError):
            jsonschema.validate({"token": "ghp_x", "repos": []}, GITHUB_PAT_SCHEMA)

    def test_pat_malformed_repo_rejected(self) -> None:
        with pytest.raises(jsonschema.ValidationError):
            jsonschema.validate({"token": "ghp_x", "repos": ["just-a-name"]}, GITHUB_PAT_SCHEMA)

    def test_pat_extra_fields_rejected(self) -> None:
        with pytest.raises(jsonschema.ValidationError):
            jsonschema.validate(
                {"token": "ghp_x", "repos": ["a/b"], "collect_variables": True},
                GITHUB_PAT_SCHEMA,
            )

    def test_helpers_apply_defaults(self) -> None:
        assert api_base_url({}) == "https://api.github.com"
        assert initial_run_limit({}) == 10
        assert api_base_url({"api_base_url": "https://custom/"}) == "https://custom/"
        assert initial_run_limit({"initial_run_limit": 50}) == 50


class TestIdentity:
    def test_uuids_are_deterministic(self) -> None:
        assert account_id("notgeorge") == account_id("notgeorge")
        assert repository_id("notgeorge/samsite") == repository_id("notgeorge/samsite")
        assert workflow_id("notgeorge/samsite", 12345) == workflow_id("notgeorge/samsite", 12345)
        assert run_id("notgeorge/samsite", 999) == run_id("notgeorge/samsite", 999)
        assert job_id("notgeorge/samsite", 8888) == job_id("notgeorge/samsite", 8888)
        assert runner_id("notgeorge/samsite", 7) == runner_id("notgeorge/samsite", 7)

    def test_different_inputs_yield_different_uuids(self) -> None:
        assert account_id("notgeorge") != account_id("someone-else")
        assert workflow_id("a/b", 1) != workflow_id("a/b", 2)
        assert workflow_id("a/b", 1) != workflow_id("c/d", 1)

    def test_edge_id_includes_endpoints(self) -> None:
        a = account_id("notgeorge")
        b = repository_id("notgeorge/samsite")
        c = repository_id("notgeorge/other")
        assert edge_id("OWNS_REPO__github_core", a, b) != edge_id("OWNS_REPO__github_core", a, c)
        assert edge_id("OWNS_REPO__github_core", a, b) != edge_id("DEFINES_WORKFLOW__github_core", a, b)


class TestParser:
    def test_minimal_workflow(self) -> None:
        yaml_text = """\
name: Deploy
on: push
jobs:
  build:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
"""
        config = parse_workflow_yaml(yaml_text)
        assert config["triggers"] == ["push"]
        assert config["jobs"][0]["id"] == "build"
        # `runs_on` is canonicalized to a list whichever of the three written forms was used
        # (req-github-core-declared-jobs-5), so one query shape answers "which jobs run on a
        # self-hosted label".
        assert config["jobs"][0]["runs_on"] == ["ubuntu-latest"]
        assert config["raw_yaml"] == yaml_text

    def test_triggers_normalized(self) -> None:
        yaml_text = "on:\n  push:\n  pull_request:\njobs: {}\n"
        config = parse_workflow_yaml(yaml_text)
        assert config["triggers"] == ["pull_request", "push"]

    def test_refs_categorized(self) -> None:
        yaml_text = """\
on: push
env:
  AWS_REGION: us-east-1
  DOMAIN_NAME: samaydlette.com
  CF_DIST: E2QWERTYUIOPAS
  IGNORED: just-some-string
jobs:
  deploy:
    runs-on: ubuntu-latest
    steps:
      - run: echo done
"""
        config = parse_workflow_yaml(yaml_text)
        refs = config["refs"]
        assert "us-east-1" in refs["aws_regions"]
        assert "samaydlette.com" in refs["domain_names"]
        assert "E2QWERTYUIOPAS" in refs["cloudfront_distribution_ids"]
        assert "just-some-string" not in refs["domain_names"]

    def test_empty_yaml_safe(self) -> None:
        config = parse_workflow_yaml("")
        assert config["triggers"] == []
        assert config["jobs"] == []
        assert config["refs"] == {"domain_names": [], "aws_regions": [], "cloudfront_distribution_ids": []}
        assert config["local_action_refs"] == []

    def test_local_action_refs_detected(self) -> None:
        """Local composite-action `uses: ./...` references are flagged for the
        collector to warn on (req-github-core-workflow-parse-3)."""
        yaml_text = """\
on: push
jobs:
  build:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: ./.github/actions/setup-foo
      - uses: ./local-helper
      - run: echo "hello"
  release:
    uses: ./.github/workflows/release.yml
"""
        config = parse_workflow_yaml(yaml_text)
        refs = config["local_action_refs"]
        # Two local-action refs in the `build` job; the `release` job's
        # `./.github/workflows/release.yml` is a reusable workflow call,
        # NOT a local action — must NOT be flagged.
        uses_strings = sorted(r["uses"] for r in refs)
        assert uses_strings == sorted(["./.github/actions/setup-foo", "./local-helper"])
        job_ids = {r["job_id"] for r in refs}
        assert job_ids == {"build"}

    def test_reusable_workflow_call_not_flagged_as_local_action(self) -> None:
        """`uses: ./.github/workflows/x.yml` is a reusable workflow call, not
        a local action — explicitly NOT flagged so an operator isn't told to
        investigate something that's a different category entirely."""
        yaml_text = """\
on: push
jobs:
  call:
    uses: ./.github/workflows/build.yaml
"""
        config = parse_workflow_yaml(yaml_text)
        assert config["local_action_refs"] == []


class TestCollectorRegistration:
    def test_collector_registered_in_tap_cares(self) -> None:
        from tap_cares.registry import get_collector

        cls = get_collector("github_core")
        assert cls.__name__ == "GithubCollector"


class TestSelfTest:
    """Self-test exercises the four readiness checks with mocked dependencies.

    Spec: plugins/github_core/specs/spec-github-core-v0.md
    (req-github-core-collector self-test). Live happy-path is exercised
    against the loaded samsite secret manually; unit tests cover the failure
    paths and the per-repo specificity that's the value-add over the default
    base-class self_test.
    """

    def test_unconfigured_when_secret_missing(self, monkeypatch) -> None:
        from tap_plugin.github_core.collectors.github_collector import collector as mod

        from tap_cares.exceptions import SecretNotFoundError

        def _raise(_ref):
            raise SecretNotFoundError("github_core/collector secret not found")

        monkeypatch.setattr(mod, "resolve_github_secret", _raise)
        result = mod.GithubCollector.self_test()
        assert result.status == "unconfigured"
        assert not result.runnable
        assert any(c.is_failure and c.code == "GITHUB_SECRET_PRESENT" for c in result.checks)

    def test_misconfigured_when_secret_schema_fails(self, monkeypatch) -> None:
        from tap_plugin.github_core.collectors.github_collector import collector as mod

        from tap_cares.exceptions import SecretValidationError

        def _raise(_ref):
            raise SecretValidationError("bad shape: 'token' missing")

        monkeypatch.setattr(mod, "resolve_github_secret", _raise)
        result = mod.GithubCollector.self_test()
        assert result.status == "misconfigured"
        assert any(c.is_failure and c.code == "GITHUB_SECRET_VALID" for c in result.checks)

    def test_per_repo_failure_surfaces_each_repo_separately(self, monkeypatch) -> None:
        """A 404 on one repo records that repo's failure by name; healthy
        repos still get their per-repo PASS row so the operator sees the
        whole picture, not just the first broken thing."""
        from tap_plugin.github_core.collectors.github_collector import collector as mod
        from tap_plugin.github_core.collectors.github_collector.api_client import GithubAPIError

        from tap_cares.secrets.models import Secret, SecretRef

        def _good_secret(_ref):
            return Secret(
                ref=SecretRef(scope="github_core", key="collector"),
                kind="github_pat",
                description="test",
                data={"token": "ghp_x", "repos": ["good/repo", "bad/repo"]},
                source_path="/test",
                metadata={},
            )

        class _StubClient:
            def __init__(self, **kwargs):
                pass

            def get(self, path, **_):
                if path == "/rate_limit":
                    return {"rate": {"limit": 5000, "used": 0}}
                if path == "/repos/bad/repo":
                    raise GithubAPIError(status=404, url=path, body='{"message":"Not Found"}')
                return {}

        monkeypatch.setattr(mod, "resolve_github_secret", _good_secret)
        monkeypatch.setattr(mod, "GithubClient", _StubClient)
        result = mod.GithubCollector.self_test()
        assert result.status == "error"
        codes = {c.code: c.is_failure for c in result.checks}
        assert codes["GITHUB_REPO_ACCESS:good/repo"] is False
        assert codes["GITHUB_REPO_ACCESS:bad/repo"] is True
        # Per-repo specificity check: the failing repo name appears in the
        # check message so an operator can act on it without reading code.
        bad_check = next(c for c in result.checks if c.code == "GITHUB_REPO_ACCESS:bad/repo")
        assert "bad/repo" in bad_check.message


class TestAccountScope:
    """req-github-core-org-scope: the account's repositories are enumerated, filtered, and
    the enumeration recorded on the run — including whether the walk was complete."""

    @staticmethod
    def _collector():
        from tap_plugin.github_core.collectors.github_collector.collector import GithubCollector

        c = GithubCollector.__new__(GithubCollector)  # no CollectorConfig needed for scope resolution
        c.results = {"info": [], "warn": [], "error": []}
        return c

    class _Client:
        def __init__(self, org_repos=None, user_repos=None, complete=True):
            self.org_repos, self.user_repos, self.complete = org_repos, user_repos, complete
            self.calls: list[str] = []
            self.last_walk_complete = True

        def get_paginated(self, path, *, params=None, item_path=None, max_pages=100):
            from tap_plugin.github_core.collectors.github_collector.api_client import GithubAPIError

            self.calls.append(path)
            if path.startswith("/orgs/"):
                if self.org_repos is None:
                    raise GithubAPIError(status=404, url=path, body='{"message":"Not Found"}')
                self.last_walk_complete = self.complete
                return [{"full_name": n} for n in self.org_repos]
            if path.startswith("/users/"):
                self.last_walk_complete = self.complete
                return [{"full_name": n} for n in (self.user_repos or [])]
            raise AssertionError(path)

    def test_repos_only_is_the_degenerate_scope(self) -> None:
        c = self._collector()
        client = self._Client(org_repos=["x/should-not-be-touched"])
        assert c._resolve_repos(client, None, ["notgeorge/samsite"]) == ["notgeorge/samsite"]
        assert client.calls == [] and c.results["info"] == []

    def test_org_enumerated_and_recorded(self) -> None:
        c = self._collector()
        client = self._Client(org_repos=["o/a", "o/b", "o/c"])
        assert c._resolve_repos(client, "o", []) == ["o/a", "o/b", "o/c"]
        assert client.calls == ["/orgs/o/repos"]
        (event,) = c.results["info"]
        assert event["message_code"] == "SCOPE_ENUMERATED"
        assert event["message_data"] == {
            "owner": "o", "account_kind": "org", "enumerated": 3, "collecting": 3, "filtered": False, "complete": True,
        }

    def test_user_fallback_on_org_404(self) -> None:
        c = self._collector()
        client = self._Client(org_repos=None, user_repos=["u/one"])
        assert c._resolve_repos(client, "u", []) == ["u/one"]
        assert client.calls == ["/orgs/u/repos", "/users/u/repos"]
        assert c.results["info"][0]["message_data"]["account_kind"] == "user"

    def test_repos_filter_over_enumeration_with_unmatched_warning(self) -> None:
        c = self._collector()
        client = self._Client(org_repos=["o/a", "o/b"])
        assert c._resolve_repos(client, "o", ["o/b", "o/zzz"]) == ["o/b"]
        (warn,) = c.results["warn"]
        assert warn["message_code"] == "SCOPE_FILTER_UNMATCHED" and warn["message_data"]["unmatched"] == ["o/zzz"]
        assert c.results["info"][0]["message_data"]["filtered"] is True

    def test_incomplete_walk_is_labelled_not_hidden(self) -> None:
        c = self._collector()
        client = self._Client(org_repos=["o/a"], complete=False)
        c._resolve_repos(client, "o", [])
        event = c.results["info"][0]
        assert event["message_data"]["complete"] is False and "INCOMPLETE" in event["message"]

    def test_self_test_owner_access_bounded_to_one_walk(self, monkeypatch) -> None:
        """Account-scoped self-test proves enumeration with ONE listing walk (no per-repo probes)."""
        from tap_plugin.github_core.collectors.github_collector import collector as mod

        from tap_cares.secrets.models import Secret, SecretRef

        def _secret(_ref):
            return Secret(
                ref=SecretRef(scope="github_core", key="collector"), kind="github_pat", description="t",
                data={"token": "ghp_x", "owner": "o"}, source_path="/test", metadata={},
            )

        calls: list[str] = []

        class _Stub:
            last_walk_complete = True

            def __init__(self, **kwargs):
                pass

            def get(self, path, **_):
                calls.append(path)
                return {"rate": {"limit": 5000, "used": 1}}

            def get_paginated(self, path, **_):
                calls.append(path)
                return [{"full_name": "o/a"}, {"full_name": "o/b"}]

        monkeypatch.setattr(mod, "resolve_github_secret", _secret)
        monkeypatch.setattr(mod, "GithubClient", _Stub)
        result = mod.GithubCollector.self_test()
        assert result.runnable, [c.message for c in result.checks]
        codes = {c.code: c.is_failure for c in result.checks}
        assert codes["GITHUB_OWNER_ACCESS:o"] is False
        assert not [k for k in codes if k.startswith("GITHUB_REPO_ACCESS:")]
        # Two /rate_limit calls, and both earn their place: the first proves the TOKEN is alive
        # on its own (liveness is per credential — a dead token beside a live App must not pass),
        # the second is the shared API-reachability check. The point of this test is the single
        # LISTING walk that follows, with no per-repo probe behind it.
        assert calls == ["/rate_limit", "/rate_limit", "/orgs/o/repos"]


class TestEnrichmentDegrade:
    def test_rule_with_uninstalled_target_type_is_skipped_not_fatal(self, monkeypatch) -> None:
        """req-github-core-grid-links-8: a composition without the target plugin (git-serious
        without aws_core) still enriches what it can and records what it skipped."""
        from tap_plugin.github_core.collectors.github_collector import enrichment as mod

        monkeypatch.setattr("tap_grid.registry.list_entity_types", lambda: ["github_core__github_repository", "github_core__github_workflow"])
        calls: list[str] = []
        monkeypatch.setattr(mod, "_fetch_source_nodes", lambda *a, **k: calls.append("fetched") or [])
        manifest = {"rules": [
            {"name": "to-aws", "source_entity_type": "github_core__github_workflow", "target_entity_type": "aws_core__aws_route53_zone",
             "target_field": "name", "edge_type": "REFERENCES_RESOURCE", "source_field": "x"},
            {"name": "from-aws", "source_entity_type": "aws_core__aws_iam_oidc_provider", "target_entity_type": "github_core__github_repository",
             "target_field": "name", "edge_type": "TRUSTS_ISSUER", "source_field": "x"},
            {"name": "to-repo", "source_entity_type": "github_core__github_workflow", "target_entity_type": "github_core__github_repository",
             "target_field": "name", "edge_type": "REFERENCES_RESOURCE", "source_field": "x"},
        ]}
        result = mod.resolve_links(link_manifest=manifest, repos=["o/r"], edge_default_dimensions={})
        assert [(r.rule_name, r.missing_entity_type) for r in result.skipped_rules] == [
            ("to-aws", "aws_core__aws_route53_zone"), ("from-aws", "aws_core__aws_iam_oidc_provider")]
        assert calls == ["fetched"]  # only the installed-target rule was evaluated


class TestEnvelopeCollapse:
    """req-github-core-org-scope: a scope's shared nodes (account, platform, OIDC issuer) are
    emitted once per repo by the per-repo walk. At 19 repos that produced 43 duplicate entity ids
    and GRIFT rejected the whole batch — every repo lost for one repeated envelope."""

    @staticmethod
    def _env(eid, name):
        return {"entity": {"entity_id": eid, "name": name}, "node": {"name": name}}

    def test_last_occurrence_wins_and_order_is_preserved(self) -> None:
        from tap_plugin.github_core.collectors.github_collector.collector import GithubCollector

        out, removed = GithubCollector._collapse_by_entity_id([
            self._env("a", "platform"), self._env("b", "repo-1"),
            self._env("a", "platform-again"), self._env("c", "repo-2"),
        ])
        assert removed == 1
        assert [e["entity"]["entity_id"] for e in out] == ["a", "b", "c"]
        assert out[0]["entity"]["name"] == "platform-again", "the freshest observation should win"

    def test_nothing_to_collapse_is_a_no_op(self) -> None:
        from tap_plugin.github_core.collectors.github_collector.collector import GithubCollector

        envs = [self._env("a", "x"), self._env("b", "y")]
        out, removed = GithubCollector._collapse_by_entity_id(envs)
        assert removed == 0 and out == envs

    def test_envelopes_without_an_id_pass_through(self) -> None:
        """An id-less envelope is a different bug; GRIFT should report it, not this helper."""
        from tap_plugin.github_core.collectors.github_collector.collector import GithubCollector

        out, removed = GithubCollector._collapse_by_entity_id([{"node": {"name": "orphan"}}, self._env("a", "x")])
        assert removed == 0 and len(out) == 2


class TestPerRepoContainment:
    """req-github-core-org-scope: at org scale a transient API error is a certainty. One bad repo
    must not discard the other eighteen — but a partially-collected run must say so, because
    tombstoning may not read absence as deletion (tap#140)."""

    def test_partial_marks_collection_incomplete(self) -> None:
        from tap_plugin.github_core.collectors.github_collector import collector as mod

        c = mod.GithubCollector.__new__(mod.GithubCollector)
        c.results = {"info": [], "warn": [], "error": []}
        c.record_warn(mod._SITE_COLLECTION_PARTIAL, "COLLECTION_PARTIAL", "x",
                      message_data={"collected": 18, "failed": ["o/bad"], "collection_complete": False})
        (w,) = c.results["warn"]
        assert w["message_data"]["collection_complete"] is False
        assert w["message_data"]["collected"] == 18


def test_collapse_is_reachable_from_an_instance() -> None:
    """Regression: the collapse helper was called as a bare function from run() while defined as a
    staticmethod, so the class-level tests passed and the live run raised NameError. Exercise the
    instance path the collector actually uses."""
    from tap_plugin.github_core.collectors.github_collector.collector import GithubCollector

    c = GithubCollector.__new__(GithubCollector)
    out, removed = c._collapse_by_entity_id([
        {"entity": {"entity_id": "a"}}, {"entity": {"entity_id": "a"}},
    ])
    assert removed == 1 and len(out) == 1


class TestDanglingEdgeGuard:
    """A workflow run can name a workflow that has since been deleted. At one repo that never
    happened; at nineteen it made GRIFT reject the batch and NOTHING landed."""

    @staticmethod
    def _edge(src, tgt, etype="EXECUTES_WORKFLOW__github_core"):
        return {"entity": {"entity_id": f"{src}->{tgt}"},
                "edge": {"from_entity_id": src, "to_entity_id": tgt, "edge_type": etype}}

    def test_edge_to_uncollected_node_is_dropped_and_named(self) -> None:
        from tap_plugin.github_core.collectors.github_collector.collector import GithubCollector

        kept, dropped = GithubCollector._drop_dangling_edges(
            [self._edge("run", "gone"), self._edge("run", "wf")], {"run", "wf"})
        assert [e["edge"]["to_entity_id"] for e in kept] == ["wf"]
        assert dropped == ["EXECUTES_WORKFLOW__github_core"]

    def test_edge_from_uncollected_node_is_also_dropped(self) -> None:
        from tap_plugin.github_core.collectors.github_collector.collector import GithubCollector

        kept, dropped = GithubCollector._drop_dangling_edges([self._edge("ghost", "wf")], {"wf"})
        assert kept == [] and len(dropped) == 1

    def test_fully_resolved_batch_is_untouched(self) -> None:
        from tap_plugin.github_core.collectors.github_collector.collector import GithubCollector

        edges = [self._edge("a", "b")]
        kept, dropped = GithubCollector._drop_dangling_edges(edges, {"a", "b"})
        assert kept == edges and dropped == []


class TestGraphQLConfigLayer:
    """req-github-core-graphql-config: the config layer (metadata, rulesets, environments, workflow
    YAML) comes from one GraphQL request; the operation layer stays REST because GitHub's GraphQL
    API exposes no workflow runs or jobs."""

    @staticmethod
    def _repo(name="o/r", files=(("ci.yml", "on: push"),), truncated=False):
        return {
            "nameWithOwner": name, "name": name.split("/")[1], "databaseId": 7,
            "isArchived": False, "isFork": False, "visibility": "PUBLIC",
            "url": f"https://github.com/{name}",
            "defaultBranchRef": {"name": "main", "target": {"oid": "abc"}},
            "object": {"entries": [
                {"name": f, "path": f, "object": {"byteSize": len(txt), "isTruncated": truncated, "text": txt}}
                for f, txt in files]},
        }

    def test_workflow_files_are_keyed_by_repo_path(self) -> None:
        from tap_plugin.github_core.collectors.github_collector.graphql_client import GithubGraphQLClient

        files = GithubGraphQLClient.workflow_files(self._repo(files=(("a.yml", "x"), ("b.yml", "y"))))
        assert files == {".github/workflows/a.yml": "x", ".github/workflows/b.yml": "y"}

    def test_truncated_blobs_are_omitted_not_half_parsed(self) -> None:
        """A partial YAML parses into a workflow that is not the one in the repository. A missing
        entry is honest; a wrong one is not."""
        from tap_plugin.github_core.collectors.github_collector.graphql_client import GithubGraphQLClient

        assert GithubGraphQLClient.workflow_files(self._repo(truncated=True)) == {}

    def test_graphql_node_shapes_like_the_rest_payload(self) -> None:
        from tap_plugin.github_core.collectors.github_collector.collector import GithubCollector

        p = GithubCollector._repo_payload_from_config(self._repo())
        assert p["full_name"] == "o/r" and p["owner"]["login"] == "o"
        assert p["default_branch"] == "main" and p["visibility"] == "public" and p["id"] == 7

    def test_workflow_config_prefers_the_prefetched_yaml(self, monkeypatch) -> None:
        """The whole point: no Contents call when the config layer already carries the file."""
        from tap_plugin.github_core.collectors.github_collector.collector import GithubCollector

        c = GithubCollector.__new__(GithubCollector)
        c._config = {"o/r": self._repo(files=(("ci.yml", "on: push\njobs: {}"),))}
        called = []
        monkeypatch.setattr(GithubCollector, "_fetch_workflow_config",
                            lambda self, cl, fn, p: called.append(p) or ("", {}))
        raw, parsed = c._workflow_config(None, "o/r", ".github/workflows/ci.yml")
        assert raw.startswith("on: push") and called == []

    def test_falls_back_to_rest_when_the_file_is_not_in_the_config_layer(self, monkeypatch) -> None:
        from tap_plugin.github_core.collectors.github_collector.collector import GithubCollector

        c = GithubCollector.__new__(GithubCollector)
        c._config = {"o/r": self._repo(files=())}
        called = []
        monkeypatch.setattr(GithubCollector, "_fetch_workflow_config",
                            lambda self, cl, fn, p: called.append(p) or ("rest", {}))
        raw, _ = c._workflow_config(None, "o/r", ".github/workflows/ci.yml")
        assert raw == "rest" and called == [".github/workflows/ci.yml"]

    def test_ghes_endpoint_is_derived_not_guessed(self) -> None:
        from tap_plugin.github_core.collectors.github_collector.graphql_client import GithubGraphQLClient

        assert GithubGraphQLClient(token="t")._endpoint == "https://api.github.com/graphql"
        ghes = GithubGraphQLClient(token="t", api_base_url="https://ghe.example/api/v3")
        assert ghes._endpoint == "https://ghe.example/api/graphql"


class TestCreateAppSkillIsHostRunnable:
    """req-github-core-app-auth: the App-creation flow runs on the OPERATOR's machine, not in the
    container, because the instance mounts its secrets root read-only and must never write its own
    credentials. Host-runnable means stdlib-only — there is no dependency set out there to lean on.
    Same discipline as `tap/git_invocation.py`."""

    @staticmethod
    def _imports(path):
        import ast, sys
        mods = set()
        for node in ast.walk(ast.parse(path.read_text())):
            if isinstance(node, ast.Import):
                mods |= {a.name.split(".")[0] for a in node.names}
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                mods.add(node.module.split(".")[0])
        return {m for m in mods if m not in sys.stdlib_module_names}

    def test_host_side_scripts_import_only_the_standard_library(self) -> None:
        from pathlib import Path as P

        skill = P(__file__).resolve().parents[1] / "skills" / "create-github-app"
        # Sibling modules in the skill directory are part of the host flow, not dependencies.
        siblings = {"manifest", "api_url", "collector_modules"}
        for name in ("create_app.py", "manifest.py", "api_url.py", "collector_modules.py"):
            extra = self._imports(skill / name) - siblings
            assert not extra, f"{name} imports non-stdlib modules: {sorted(extra)}"
        # What the host flow PATH-LOADS runs on the operator's machine too, so it is held to the
        # same rule: the credential fold must stay stdlib-only (github-core#25).
        shape = P(__file__).resolve().parents[1] / "collectors" / "github_collector" / "credential_shape.py"
        assert not self._imports(shape), "credential_shape.py must stay stdlib-only"

    def test_api_base_url_must_be_https_and_bare(self) -> None:
        """req-github-core-app-auth-4 / the SonarCloud SSRF finding on PR #3: the API base URL
        reaches `urlopen` from outside the program — a CLI flag in create_app, an envelope field
        in verify_app. `urlopen` honours whatever scheme it is handed, so an `http://` base sends
        the one-time manifest code (which converts into the App's private key) in cleartext to a
        host of the caller's choosing, and a `file://` base turns the exchange into a local read."""
        import importlib.util
        from pathlib import Path as P

        mod_path = P(__file__).resolve().parents[1] / "skills" / "create-github-app" / "api_url.py"
        spec = importlib.util.spec_from_file_location("gs_api_url", mod_path)
        assert spec and spec.loader
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)

        assert mod.validate_api_base_url("https://api.github.com") == "https://api.github.com"
        assert mod.validate_api_base_url("https://api.github.com/") == "https://api.github.com"
        # GitHub Enterprise Server keeps its path.
        assert mod.validate_api_base_url("https://ghe.example.com/api/v3") == "https://ghe.example.com/api/v3"

        for bad in (
            "http://api.github.com",          # credential-bearing exchange in cleartext
            "file:///etc/passwd",             # urlopen reads local files
            "ftp://example.com/x",
            "api.github.com",                 # no scheme
            "https://",                       # no host
            "https://u:p@evil.example",       # credentials in the URL
            "https://api.github.com?x=1",     # query smuggling
        ):
            with pytest.raises(ValueError):
                mod.validate_api_base_url(bad)

    def test_permission_keys_cannot_collide_across_surfaces(self) -> None:
        """The bug this assertion exists for: repository:administration and
        organization:administration both map to a bare `administration` key unless the org surface
        is namespaced, and one silently overwrites the other."""
        import importlib.util
        from pathlib import Path as P

        skill = P(__file__).resolve().parents[1] / "skills" / "create-github-app" / "manifest.py"
        spec = importlib.util.spec_from_file_location("gs_manifest", skill)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        built = mod.build(org="o", redirect_url="http://127.0.0.1:1/callback", name="n",
                          public=False, exploratory=["organization:administration:read"])
        keys = built["default_permissions"]
        assert "administration" in keys and "organization_administration" in keys
        assert keys["administration"] == "read" and keys["organization_administration"] == "read"
