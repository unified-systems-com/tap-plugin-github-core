"""The credential envelope has ONE fold, and every reader of the envelope goes through it.

Django-free on purpose: these path-load the stdlib-only modules exactly as the host-side skill
scripts do, so the assertions hold on the operator's machine too (req-github-core-app-auth-5).

github-core#25: `verify_app.py` kept a private `kind == "github_app"` check after `create_app.py`
moved to the combined `github` kind, so it refused every credential the creation flow placed with
"nothing to verify" — a verifier that passes by never looking. The cure is structural: the fold
lives in one stdlib-only module (`credential_shape.py`), the collector imports it, and both scripts
path-load it.
"""

from __future__ import annotations

import importlib.util
import json
import re
import sys
from pathlib import Path
from types import ModuleType

import pytest

_PLUGIN = Path(__file__).resolve().parents[1]
_SKILL = _PLUGIN / "skills" / "create-github-app"
_COLLECTOR = _PLUGIN / "collectors" / "github_collector"


def _load(path: Path, name: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader, path
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture()
def skill_on_path(monkeypatch: pytest.MonkeyPatch):
    """The scripts import their siblings bare (`import api_url`), as they do when run in place."""
    monkeypatch.syspath_prepend(str(_SKILL))
    for name in ("api_url", "manifest", "collector_modules"):
        monkeypatch.delitem(sys.modules, name, raising=False)


APP = {"app_id": 7, "app_slug": "tap-observer", "private_key": "-----BEGIN PEM-----"}


class TestTheFold:
    def test_current_kind_passes_through_untouched(self) -> None:
        shape = _load(_COLLECTOR / "credential_shape.py", "cs_a")
        data = {"owner": "acme", "app": APP, "pat": {"token": "ghp_x"}}
        assert shape.normalize_credentials("github", data) == data

    def test_legacy_kinds_fold_forward(self) -> None:
        shape = _load(_COLLECTOR / "credential_shape.py", "cs_b")
        assert shape.normalize_credentials("github_pat", {"token": "ghp_x", "owner": "acme"}) == {
            "owner": "acme",
            "pat": {"token": "ghp_x"},
        }
        assert shape.normalize_credentials("github_app", {**APP, "owner": "acme"}) == {
            "owner": "acme",
            "app": APP,
        }

    def test_an_empty_legacy_token_does_not_become_a_pat_block(self) -> None:
        """Copilot on PR #26: an empty `token` must not read as "a token is present"."""
        shape = _load(_COLLECTOR / "credential_shape.py", "cs_d")
        assert shape.normalize_credentials("github_pat", {"token": "", "owner": "acme"}) == {"owner": "acme"}

    def test_unknown_kind_folds_to_scope_only(self) -> None:
        """Refusing a kind by name is `resolve_github_secret`'s job; the fold just has no
        credential to offer, so `has_app` / `has_pat` both answer no."""
        shape = _load(_COLLECTOR / "credential_shape.py", "cs_c")
        folded = shape.normalize_credentials("github_oauth", {"owner": "acme", "token": "x"})
        assert folded == {"owner": "acme"}

    def test_the_fold_is_defined_exactly_once(self) -> None:
        """secret.py re-exports; the scripts path-load. Nobody carries a second copy."""
        definition = re.compile(r"^def normalize_credentials\(", re.MULTILINE)
        definers = [p for p in _PLUGIN.rglob("*.py") if definition.search(p.read_text())]
        assert definers == [_COLLECTOR / "credential_shape.py"], definers
        for script in ("verify_app.py", "create_app.py"):
            assert "credential_shape" in (_SKILL / script).read_text(), script
        assert "credential_shape" in (_COLLECTOR / "secret.py").read_text()


class TestVerifyAppReadsTheShippedEnvelope:
    """The exact regression of github-core#25, on the script's envelope reader."""

    @pytest.fixture()
    def verify(self, skill_on_path) -> ModuleType:
        return _load(_SKILL / "verify_app.py", "verify_app_under_test")

    def test_the_envelope_create_app_writes_is_verifiable(self, verify: ModuleType) -> None:
        envelope = {"kind": "github", "data": {"owner": "acme", "api_base_url": "https://api.github.com", "app": APP}}
        folded = verify.app_credentials(envelope)
        assert folded is not None
        assert folded["app"] == APP
        assert folded["owner"] == "acme"

    def test_a_legacy_app_envelope_still_verifies(self, verify: ModuleType) -> None:
        assert verify.app_credentials({"kind": "github_app", "data": {**APP, "owner": "acme"}}) is not None

    def test_a_token_only_envelope_is_named_as_such(self, verify: ModuleType) -> None:
        envelope = {"kind": "github", "data": {"owner": "acme", "pat": {"token": "ghp_x"}}}
        assert verify.app_credentials(envelope) is None
        assert "personal access token but no GitHub App" in verify.describe_missing_app(envelope)

    def test_an_unknown_kind_is_reported_not_crashed(self, verify: ModuleType) -> None:
        envelope = {"kind": "github_oauth", "data": {"owner": "acme"}}
        assert verify.app_credentials(envelope) is None
        assert "'github_oauth'" in verify.describe_missing_app(envelope)

    @pytest.mark.parametrize(
        "envelope",
        [
            {"kind": "github", "data": "not-an-object"},
            {"kind": "github", "data": ["owner"]},
            {"kind": "github", "data": {"owner": "acme", "app": "not-an-object"}},
            {"kind": "github"},
        ],
    )
    def test_a_malformed_envelope_is_explained_not_raised(self, verify: ModuleType, envelope: dict) -> None:
        """Copilot on PR #26: this path exists to SAY why verification cannot proceed."""
        assert verify.app_credentials(envelope) is None
        assert "github" in verify.describe_missing_app(envelope)

    def test_no_private_kind_check_survives(self, verify: ModuleType) -> None:
        text = (_SKILL / "verify_app.py").read_text()
        assert '!= "github_app"' not in text


class TestTheLoader:
    def test_a_missing_collector_module_names_the_broken_checkout(self, skill_on_path) -> None:
        loader = _load(_SKILL / "collector_modules.py", "collector_modules_under_test")
        with pytest.raises(SystemExit, match="broken checkout"):
            loader.load("no_such_module")


class TestCreateAppCarriesTheTokenThroughTheFold:
    @pytest.fixture()
    def create_app(self, skill_on_path) -> ModuleType:
        return _load(_SKILL / "create_app.py", "create_app_under_test")

    @staticmethod
    def _place(create_app: ModuleType, root: Path, existing: dict | None) -> dict:
        target = root / "github_core" / "collector.secret.json"
        if existing is not None:
            target.parent.mkdir(parents=True)
            target.write_text(json.dumps(existing))
        create_app.place_envelope(
            {"id": 7, "slug": "tap-observer", "pem": "-----BEGIN PEM-----"},
            owner="acme",
            api_base_url="https://api.github.com",
            secrets_root=root,
        )
        return json.loads(target.read_text())

    def test_legacy_pat_envelope_token_is_carried(self, create_app: ModuleType, tmp_path: Path) -> None:
        written = self._place(create_app, tmp_path, {"kind": "github_pat", "data": {"owner": "acme", "token": "ghp_x"}})
        assert written["kind"] == "github"
        assert written["data"]["pat"] == {"token": "ghp_x"}
        assert written["data"]["app"]["app_id"] == 7

    def test_combined_envelope_pat_block_is_carried(self, create_app: ModuleType, tmp_path: Path) -> None:
        written = self._place(
            create_app, tmp_path, {"kind": "github", "data": {"owner": "acme", "pat": {"token": "ghp_y"}}}
        )
        assert written["data"]["pat"] == {"token": "ghp_y"}

    def test_nothing_to_carry_writes_no_pat_block(self, create_app: ModuleType, tmp_path: Path) -> None:
        written = self._place(create_app, tmp_path, None)
        assert "pat" not in written["data"]
