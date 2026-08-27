"""GitHub App credential mechanics: sign a JWT, exchange it for an installation token.

**The one implementation of this derivation.** Both sides need it — the operator's host-side
verification flow (`skills/create-github-app/verify_app.py`, which proves a placed credential
before anything trusts it) and the collector's auth seam (`auth.py`, which uses it every run) —
so it lives here alone and the host side path-imports this file. A second copy is how the two
would drift into disagreeing about what the credential can do.

Deliberately narrow: standard library plus `cryptography`, no TAP imports, no Django. That is what
lets the host side load it from a checkout with nothing installed.

Signing is RS256 through `cryptography` against the system OpenSSL the FIPS posture validates
(`specs/spec-fips.md`) — App auth introduces no new cryptographic provider, which is the whole
reason no JWT library is used.
"""

from __future__ import annotations

import base64
import json
import time
import urllib.error
import urllib.request
from typing import Any

#: GitHub rejects a JWT whose life exceeds 10 minutes. Nine leaves room for the backdating below.
_JWT_LIFETIME_SECONDS = 540
#: Backdate `iat` so a slightly fast local clock does not mint a token GitHub reads as future-dated.
_CLOCK_SKEW_SECONDS = 60
_TIMEOUT_SECONDS = 30


class GithubAppAuthError(Exception):
    """The App credential could not be turned into a usable token."""


def _b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def mint_jwt(app_id: int | str, private_key_pem: str) -> str:
    """Sign the short-lived JWT that identifies the APPLICATION (not an installation).

    This token authenticates App-level endpoints — `/app/installations` and the token exchange —
    and nothing else. Repository data needs the installation token `exchange_installation_token`
    returns.
    """
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import padding

    now = int(time.time())
    header = _b64(json.dumps({"alg": "RS256", "typ": "JWT"}, separators=(",", ":")).encode())
    claims = _b64(
        json.dumps(
            {"iat": now - _CLOCK_SKEW_SECONDS, "exp": now + _JWT_LIFETIME_SECONDS, "iss": str(app_id)},
            separators=(",", ":"),
        ).encode()
    )
    signing_input = f"{header}.{claims}".encode()
    key = serialization.load_pem_private_key(private_key_pem.encode(), password=None)
    signature = key.sign(signing_input, padding.PKCS1v15(), hashes.SHA256())
    return f"{header}.{claims}.{_b64(signature)}"


def app_get(api_base_url: str, path: str, jwt: str, *, method: str = "GET") -> Any:
    """Call an App-level endpoint with the App JWT, returning the decoded body.

    Raises `GithubAppAuthError` on any non-2xx, with the status and a short body excerpt — the
    body is GitHub's own error text, never credential material.
    """
    url = f"{api_base_url.rstrip('/')}{path}"
    request = urllib.request.Request(
        url,
        method=method,
        headers={
            "Authorization": f"Bearer {jwt}",
            "Accept": "application/vnd.github+json",
            "User-Agent": "tap-github-core-collector",
            "X-GitHub-Api-Version": "2022-11-28",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=_TIMEOUT_SECONDS) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        raise GithubAppAuthError(f"App endpoint {path} returned {exc.code}: {exc.read()[:200]!r}") from exc
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise GithubAppAuthError(f"App endpoint {path} unreachable: {exc}") from exc


def list_installations(api_base_url: str, jwt: str) -> list[dict[str, Any]]:
    """Every account this App is installed into.

    App-only: a personal access token gets `404` from this endpoint, which is one of the two
    surfaces that make the App the product credential rather than a convenience.
    """
    body = app_get(api_base_url, "/app/installations", jwt)
    return [i for i in body if isinstance(i, dict)] if isinstance(body, list) else []


def exchange_installation_token(api_base_url: str, jwt: str, installation_id: int | str) -> tuple[str, str]:
    """Trade the App JWT for one installation's access token.

    Returns ``(token, expires_at)``. The token carries only the permissions that installation was
    granted, scoped to that account — which is why it must never be cached anywhere an unrelated
    installation could reach it.
    """
    body = app_get(api_base_url, f"/app/installations/{installation_id}/access_tokens", jwt, method="POST")
    if not isinstance(body, dict) or not body.get("token"):
        raise GithubAppAuthError(f"installation {installation_id} returned no token")
    return str(body["token"]), str(body.get("expires_at") or "")
