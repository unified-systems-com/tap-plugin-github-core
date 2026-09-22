"""github_core is core's first adopter of ASSIGNED identity (Issue# 162 - tap-plugin-github-core).

The mechanism under test, end to end through `run_collection` exactly as the worker runs it:

1. every model declares how one of its rows is found again (`NATURAL_KEY`);
2. the collector names each node with a batch-local `ref` instead of a minted id;
3. the importer resolves each ref inside the batch transaction — `find_existing` under an
   advisory lock, a fresh UUIDv7 on a miss (`tap_grid/services/__init__.py::resolve_identity`);
4. a SECOND run over the same source finds the first run's rows and reuses their ids.

These tests prove the mechanism rather than the absence of breakage, which is why they assert
positively on all three halves:

- **stability** — run two over an unchanged fake GitHub produces the same entity id per source
  object, which is the whole point of declaring a natural key;
- **assignment** — those ids are UUIDv7, i.e. core minted them, and are NOT the UUIDv5 the
  plugin used to derive for the same key. A test that only checked stability would stay green
  if the collector had quietly kept deriving ids, since a derivation is stable too;
- **the wire** — the document the collector builds carries `ref` / `from_ref`, not
  `entity_id`, so the id is core's to assign at every step and not just at the end.
"""

from __future__ import annotations

import inspect
from typing import Any
from uuid import UUID, uuid5

import pytest
from tap_plugin.github_core.collectors.github_collector import collector as collector_module
from tap_plugin.github_core.collectors.github_collector.collector import GithubCollector
from tap_plugin.github_core.collectors.github_collector.graphql_client import GithubGraphQLClient
from tap_plugin.github_core.collectors.github_collector.identity import (
    GITHUB_CORE_NAMESPACE,
    Ref,
    account_id,
    repository_id,
    workflow_id,
    workflow_job_id,
)
from tap_plugin.github_core.models.github_account import GithubAccount
from tap_plugin.github_core.models.github_environment import GithubEnvironment
from tap_plugin.github_core.models.github_platform import GithubPlatform
from tap_plugin.github_core.models.github_repository import GithubRepository
from tap_plugin.github_core.models.github_workflow import GithubWorkflow
from tap_plugin.github_core.models.workflow_job import WorkflowJob

from tap.pytest_harness import isolated_registry
from tap_cares.models import CollectionJobStatus, Collector
from tap_cares.registry import collector_registry
from tap_cares.services import run_collection
from tap_grid.models import Entity

from .fake_estate import OWNER, REPO, Secret, config_repo, fake_rest, register

#: The source objects one run over the shared fake estate observes, each named by the natural
#: key its model declares. One row per model, so a declaration that cannot find its own row is
#: caught by name.
SOURCE_OBJECTS: list[tuple[str, Any, dict[str, Any]]] = [
    ("platform", GithubPlatform, {"host": "github.com"}),
    ("account", GithubAccount, {"login": OWNER}),
    ("repository", GithubRepository, {"full_name": REPO}),
    ("workflow", GithubWorkflow, {"full_name": REPO, "workflow_id": 100}),
    ("environment", GithubEnvironment, {"full_name": REPO, "name": "production"}),
    ("declared job", WorkflowJob, {"full_name": REPO, "workflow_id": 100, "job_key": "build"}),
]

def _run(monkeypatch: pytest.MonkeyPatch, collector: Collector | None = None) -> Collector:
    """One collection run against the fake GitHub, through the task body.

    Returns the registered collector so a second run can reuse it: the registry refuses a
    duplicate key, and running twice is the whole point here.
    """
    collector = collector or register(GithubCollector)
    fake = fake_rest()
    monkeypatch.setattr(collector_module, "resolve_github_secret", lambda *a, **k: Secret())
    monkeypatch.setattr(collector_module, "GithubClient", lambda **kw: fake)
    monkeypatch.setattr(GithubGraphQLClient, "fetch_config_layer", lambda self, login: ([config_repo()], []))
    monkeypatch.setattr(GithubGraphQLClient, "fetch_pull_request_layer", lambda self, login: ({}, []))
    job = run_collection(collector)
    job.refresh_from_db()
    assert job.status == CollectionJobStatus.SUCCESSFUL.value, job.summary
    return collector


def _ids() -> dict[str, UUID]:
    """One live row per source object, found through the key its model declares."""
    found: dict[str, UUID] = {}
    for label, model, natural_key in SOURCE_OBJECTS:
        rows = list(model.objects.live().filter(**natural_key))
        assert len(rows) == 1, f"{label}: expected exactly one live row for {natural_key}, found {len(rows)}"
        found[label] = rows[0].entity_id
    return found


def _former_uuid5(entity_type: str, natural_key: str) -> UUID:
    """The id this plugin used to derive for a key, spelled out rather than imported.

    Written here as a literal derivation on purpose: importing it from the collector would make
    this assertion true by construction the moment someone reintroduced derived ids under a new
    name. This is the shape the code no longer has.
    """
    return uuid5(GITHUB_CORE_NAMESPACE, f"{entity_type}:{natural_key}")


@pytest.mark.django_db(transaction=True, databases=["default", "search_readonly"])
@pytest.mark.spec("req-grid-entity-natural-key-9")
class TestTwoRunsOverOneSource:
    def test_the_same_source_object_keeps_the_same_assigned_id_across_runs(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Run two finds run one's rows through the declared key and writes under their ids.

        The done-test of the adoption (Issue# 162). Nothing in the second run's document names
        an id: it sends the same refs, and `resolve_identity` answers them with the rows the
        first run created. A declaration that failed to find its own row would show up here as
        a second id for the same source object — and, because a second live row is what a
        search refuses to choose between, most likely as a failed batch first.
        """
        with isolated_registry(collector_registry):
            collector = _run(monkeypatch)
            first = _ids()
            _run(monkeypatch, collector)
            second = _ids()

        assert second == first, "a second run over an unchanged source re-used every row it found"

    def test_the_ids_are_assigned_by_core_not_derived_by_the_plugin(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """UUIDv7 and NOT the former UUIDv5 — stability alone would not prove assignment.

        A derived id is stable too, so the test above would stay green if the collector had
        quietly kept minting. Version 7 says core's `uuid7()` produced it; the inequality says
        it is not the old derivation wearing a new hat. Both halves, because either alone is
        satisfiable by the thing this change removed.
        """
        with isolated_registry(collector_registry):
            _run(monkeypatch)
            found = _ids()

        for label, entity_id in found.items():
            assert entity_id.version == 7, f"{label}: {entity_id} is not a UUIDv7, so core did not assign it"

        assert found["repository"] != _former_uuid5("github_core__github_repository", REPO)
        assert found["account"] != _former_uuid5("github_core__github_account", OWNER)
        assert found["workflow"] != _former_uuid5("github_core__github_workflow", f"{REPO}#100")
        assert found["declared job"] != _former_uuid5("github_core__workflow_job", f"{REPO}#100#build")
        assert found["platform"] != _former_uuid5("github_core__github_platform", "github.com")

    def test_no_entity_on_the_grid_carries_a_ref(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """"A ref never reaches a record" (`tap_grid/grift/refs.py`) — asserted, not assumed.

        The refs the collector sends are namespaced `<entity_type>:<key>` strings, which are not
        UUIDs at all; if one ever survived resolution it would be visible as an entity whose id
        is not parseable, or as a row whose declared values are a ref string. This asserts the
        only thing a collector can assert from outside: every id on the grid is a real UUID and
        no declared value looks like a ref.
        """
        with isolated_registry(collector_registry):
            _run(monkeypatch)
            ids = list(Entity.objects.values_list("id", flat=True))
            names = list(GithubRepository.objects.live().values_list("full_name", flat=True))

        assert ids and all(isinstance(i, UUID) for i in ids)
        assert all(not str(n).startswith("github_core__") for n in names)


class TestTheDocumentItself:
    """What the collector puts on the wire, checked without a database.

    The tests above would also pass if the collector sent ids that core happened to accept. This
    one reads the envelope: every node this collector names, names itself by `ref`, so the id is
    core's to assign. Edge ids stay derived on purpose — edges are KEYLESS, so the derivation is
    the plugin's cross-run edge idempotency.
    """

    def test_a_github_core_node_names_itself_by_ref(self) -> None:
        for fn, args in (
            (repository_id, (REPO,)),
            (account_id, (OWNER,)),
            (workflow_id, (REPO, 100)),
            (workflow_job_id, (REPO, 100, "build")),
        ):
            value = fn(*args)  # type: ignore[operator]
            assert isinstance(value, Ref), f"{fn.__name__} still mints an id instead of naming a ref"
            assert not isinstance(value, UUID)

    def test_the_held_back_set_is_empty(self) -> None:
        """Nothing is held back any more — asserted over the MODULE, not over a list kept by hand.

        Held back was a state, and it is over as of 2026-09-20. Rather than deleting the test
        that recorded it, it now asserts the opposite property positively: every public
        `*_id` recipe in `identity.py` that names a NODE returns a `Ref`, so a future function
        that quietly minted a UUID instead would fail here rather than in a grid.

        Each of the last three was released by making the declared search able to filter the
        same value the ref composes. `commit_observation` gained a `host` field (Issue# 164).
        `actions_secret` stored the canonical upper-cased spelling (Issue# 165). And
        `code_scanning_finding` waited on compliance_core — the plugin that OWNS the type — to
        declare (Issue# 8 - tap-plugin-compliance-core), because a declaration lives on the
        owning model and nothing done in this repository could substitute for it.
        """
        from tap_plugin.github_core.collectors.github_collector import identity as identity_module
        from tap_plugin.github_core.collectors.github_collector.identity import (
            actions_secret_id,
            code_scanning_finding_id,
            commit_observation_id,
        )

        # Edge ids stay derived on purpose (edges are KEYLESS, and the id is the plugin's
        # cross-run edge idempotency), so they are excluded by name rather than by accident.
        edge_recipes = {"edge_id", "uses_action_edge_id"}
        minted: dict[str, str] = {}
        for name in dir(identity_module):
            if not name.endswith("_id") or name.startswith("_") or name in edge_recipes:
                continue
            fn = getattr(identity_module, name)
            if not callable(fn):
                continue
            hints = getattr(fn, "__annotations__", {})
            if hints.get("return") not in ("Ref", Ref):
                minted[name] = f"annotated {hints.get('return')!r}"
            elif "_uuid5_id" in inspect.getsource(fn):
                # The annotation alone would be a presence test: a function annotated `-> Ref`
                # that returned the pre-assignment derivation would pass it (Codex on PR# 166 -
                # github-core). `_uuid5_id` IS that derivation, and reaching it is the realistic
                # way a node recipe regresses, so the body is read too.
                minted[name] = "annotated Ref but reaches the uuid5 derivation"
        assert minted == {}, f"these node recipes do not name a ref: {minted}"

        # The three that were released, named so the set cannot silently regrow.
        assert isinstance(code_scanning_finding_id(REPO, 7), Ref)
        assert isinstance(commit_observation_id("github.com", 10, "sha1", "a" * 40), Ref)
        assert isinstance(actions_secret_id("repository", OWNER, REPO, "", "HARNESS_PAT"), Ref)
        # The fold is the identity, so two spellings must compose the SAME ref.
        assert actions_secret_id("repository", OWNER, REPO, "", "harness_pat") == actions_secret_id(
            "repository", OWNER, REPO, "", "HARNESS_PAT"
        )

    def test_the_finding_ref_and_the_key_it_writes_are_one_string(self) -> None:
        """compliance_core decides the key; github_core owes the VALUES its declaration names.

        `compliance_core__compliance_finding` declares `("source", "source_key")`, so the
        importer's generated search filters those two columns on the node this collector
        writes. The ref is a batch-local label and does NOT participate in identity — which is
        exactly why the written `source_key` and the ref must be composed in one place, or the
        label and the search could name different objects with nothing to notice.
        """
        from tap_plugin.github_core.collectors.github_collector.identity import (
            COMPLIANCE_FINDING_SOURCE,
            code_scanning_finding_id,
            code_scanning_finding_source_key,
        )

        assert COMPLIANCE_FINDING_SOURCE == "github_core", "the namespace half is this plugin's slug"
        key = code_scanning_finding_source_key(REPO, 7)
        assert code_scanning_finding_id(REPO, 7) == f"compliance_core__compliance_finding:{key}"

    def test_the_adoption_does_not_change_a_single_edge_id(self) -> None:
        """The property that makes this change data-neutral on an ALREADY-POPULATED grid.

        The importer finds an edge by the `entity_id` the envelope supplies and by nothing else
        (`tap_grid/grift/importer.py`, the `edge_exists` branch), so an edge id that shifted
        would make the first re-collect CREATE a second live edge for every fact that already
        has one rather than replace it — one silent doubling per grid (Codex on PR# 163). Node
        ids are safe for the opposite reason: `find_existing` matches the existing typed rows on
        their declared fields, so they are found rather than minted.

        Pinned to literals taken from before the adoption. `_endpoint_token` re-derives the node
        id each ref used to be, which is why these still hold; if that step is ever removed,
        this is the test that says what it costs.
        """
        from tap_plugin.github_core.collectors.github_collector.identity import (
            edge_id,
            github_action_id,
            uses_action_edge_id,
        )

        assert (
            str(uses_action_edge_id(workflow_job_id("o/r", 1, "build"), github_action_id("actions/checkout"), "v4"))
            == "b8e8a365-d0d1-574a-9363-e6ea8a917d1a"
        )
        assert (
            str(edge_id("OWNS_REPO__github_core", account_id("acme"), repository_id("acme/app")))
            == "b88a3925-3fe1-547c-bfa0-febb3da711a6"
        )

    def test_every_github_core_model_has_declared(self) -> None:
        """Undeclared is never keyless: a ref to an undeclared type is refused outright.

        `resolve_identity` raises rather than guessing, so a model added later without a
        declaration fails the whole batch at run time. This is the same check at author time.
        """
        from tap_grid.registry import get_model_class, list_entity_types

        undeclared = [
            t
            for t in list_entity_types()
            if t.startswith("github_core__") and getattr(get_model_class(t), "NATURAL_KEY", None) is None
        ]
        assert undeclared == [], f"these types would be refused a ref: {undeclared}"
