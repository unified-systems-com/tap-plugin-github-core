"""The collector's auth seam: one object that yields a bearer token, whatever the envelope is.

Two credential kinds reach this plugin and neither is privileged above the other in code:

* `github_pat` — a person's power in token form. Right for pointing an instance at one repository
  in ten minutes; it inherits its holder's role and dies when they leave.
* `github_app` — the App is its own principal with its own declared permissions, and two surfaces
  the product needs are App-ONLY (the account's installed-App inventory and its fine-grained PAT
  grants both answer `404` to any token). It is the product credential.

Everything above this seam asks for `auth.token()` and does not know which it got. What it MAY ask
is `auth.mode`, and only to decide whether an App-only surface is worth attempting — asking is
cheaper than a 404 and far cheaper than reporting an empty inventory as a fact.

Spec: plugins/github_core/specs/spec-github-core-v0.md (req-github-core-app-auth)
"""

from __future__ import annotations

import logging
from typing import Any

from .app_jwt import GithubAppAuthError, exchange_installation_token, list_installations, mint_jwt

logger = logging.getLogger(__name__)

MODE_PAT = "pat"
MODE_APP = "app"


class GithubAuth:
    """Resolved credential → bearer token, plus the App-only facts a PAT cannot reach.

    Installation tokens are held on the INSTANCE, never at module or class scope. Two collections
    running against two accounts in one process would otherwise be one shared mutable token away
    from cross-account leakage — a failure that produces plausible results rather than an error,
    which is the worst kind (`req-github-core-app-auth-7`).
    """

    def __init__(self, *, kind: str, data: dict[str, Any], api_base_url: str) -> None:
        self._kind = kind
        self._data = data
        self._api_base_url = api_base_url
        self._token: str | None = None
        self._installation: dict[str, Any] | None = None
        self._installations: list[dict[str, Any]] | None = None

    @property
    def mode(self) -> str:
        """`pat` or `app` — what kind of principal this collection is running as."""
        return MODE_APP if self._kind == "github_app" else MODE_PAT

    @property
    def installation(self) -> dict[str, Any] | None:
        """The installation whose token is in use, once one has been minted (App mode only)."""
        return self._installation

    def token(self) -> str:
        """A bearer token for repository-scoped calls.

        PAT mode returns the token as placed. App mode mints an installation token on first use
        and reuses it for the run — GitHub's installation tokens last about an hour, comfortably
        longer than a collection, and re-minting per call would burn rate limit to no purpose.
        """
        if self.mode == MODE_PAT:
            return str(self._data["token"])
        if self._token is None:
            self._token = self._mint_installation_token()
        return self._token

    def app_jwt(self) -> str:
        """The App-level JWT. App mode only — a PAT cannot produce one."""
        if self.mode != MODE_APP:
            raise GithubAppAuthError("app_jwt() requires a github_app credential")
        return mint_jwt(self._data["app_id"], self._data["private_key"])

    def installations(self) -> list[dict[str, Any]]:
        """Every installation of this App, or an empty list in PAT mode.

        Empty means *nothing observed*, and the caller must not render it as *nothing installed*:
        in PAT mode the surface is unreachable, not empty. The collector records which it was.
        """
        if self.mode != MODE_APP:
            return []
        if self._installations is None:
            self._installations = list_installations(self._api_base_url, self.app_jwt())
        return self._installations

    def _mint_installation_token(self) -> str:
        """Pick this account's installation and trade the JWT for its token.

        The envelope's `owner` selects the installation rather than "the first one": an App
        installed into several accounts would otherwise collect one account's repositories under
        another account's name, silently and plausibly.
        """
        installations = self.installations()
        if not installations:
            raise GithubAppAuthError("App has no installations — nothing to collect as")
        owner = str(self._data.get("owner") or "")
        chosen: dict[str, Any] | None = None
        if owner:
            chosen = next(
                (i for i in installations if str((i.get("account") or {}).get("login", "")).lower() == owner.lower()),
                None,
            )
            if chosen is None:
                available = ", ".join(sorted(str((i.get("account") or {}).get("login", "?")) for i in installations))
                raise GithubAppAuthError(
                    f"App is not installed on {owner!r} (installed on: {available or 'nothing'})"
                )
        else:
            # A repos-only envelope names no account. One installation is unambiguous; several are
            # not, and guessing would attribute one account's data to another.
            if len(installations) > 1:
                raise GithubAppAuthError(
                    "App has several installations and the envelope names no `owner` to choose between them"
                )
            chosen = installations[0]
        self._installation = chosen
        token, expires_at = exchange_installation_token(self._api_base_url, self.app_jwt(), chosen["id"])
        logger.info(
            "[17b1] minted installation token for %s (installation %s), expires %s",
            (chosen.get("account") or {}).get("login", "?"),
            chosen.get("id"),
            expires_at or "unknown",
        )
        return token
