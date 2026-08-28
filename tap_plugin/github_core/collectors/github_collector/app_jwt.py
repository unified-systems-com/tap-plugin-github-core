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
import urllib.parse
import urllib.request
from typing import Any

#: GitHub rejects a JWT whose life exceeds 10 minutes. Nine leaves room for the backdating below.
_JWT_LIFETIME_SECONDS = 540
#: Backdate `iat` so a slightly fast local clock does not mint a token GitHub reads as future-dated.
_CLOCK_SKEW_SECONDS = 60
_TIMEOUT_SECONDS = 30
#: A backstop on the installation walk. 100 pages is 10,000 installations — far past any real App,
#: and a bound is better than a loop that trusts a header.
_MAX_INSTALLATION_PAGES = 100

#: The `Link` header of the most recent response, so the pagination walk can see whether another
#: page exists. A single-element list rather than a global rebind: this module is deliberately
#: dependency-free and has no client object to hang it on.
_LAST_LINK_HEADER: list[str] = [""]


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


def _validate_base_url(api_base_url: str) -> str:
    """Refuse a base URL that is not https, before any credential moves.

    `urlopen` honours whatever scheme it is handed: an `http://` base would send the App JWT — and
    the installation token minted from it — in cleartext to a host of the envelope's choosing, and
    a `file://` base would turn an API call into a local file read. The envelope's schema refuses
    both at load; this refuses them again at the call, because the value crosses a trust boundary
    and one check on each side of it is cheap. (This is also what satisfies Bandit's B310 audit of
    the `urlopen` below.)
    """
    parts = urllib.parse.urlsplit(api_base_url)
    if parts.scheme != "https":
        raise GithubAppAuthError(
            f"api_base_url must be https (got {parts.scheme or 'no'} scheme): {api_base_url!r}"
        )
    if not parts.hostname:
        raise GithubAppAuthError(f"api_base_url has no host: {api_base_url!r}")
    return api_base_url.rstrip("/")


def app_get(api_base_url: str, path: str, jwt: str, *, method: str = "GET") -> Any:
    """Call an App-level endpoint with the App JWT, returning the decoded body.

    Raises `GithubAppAuthError` on any non-2xx, with the status and a short body excerpt — the
    body is GitHub's own error text, never credential material.
    """
    url = f"{_validate_base_url(api_base_url)}{path}"
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
        # nosec B310 — `url` is built from _validate_base_url() above, which refuses any
        # scheme but https. Both spellings: ruff reads noqa, Bandit reads nosec.
        with urllib.request.urlopen(request, timeout=_TIMEOUT_SECONDS) as response:  # noqa: S310  # nosec B310
            body = json.loads(response.read().decode("utf-8"))
            _LAST_LINK_HEADER[0] = response.headers.get("Link", "")
            return body
    except urllib.error.HTTPError as exc:
        raise GithubAppAuthError(f"App endpoint {path} returned {exc.code}: {exc.read()[:200]!r}") from exc
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise GithubAppAuthError(f"App endpoint {path} unreachable: {exc}") from exc


def list_installations(api_base_url: str, jwt: str) -> list[dict[str, Any]]:
    """EVERY account this App is installed into, following pagination to the end.

    App-only: a personal access token gets `404` from this endpoint, which is one of the two
    surfaces that make the App the product credential rather than a convenience.

    The walk matters more than it looks. This list is what `owner` is matched against to pick an
    installation, so a truncated page does not produce a short list — it produces "App is not
    installed on <account>" for an account it *is* installed on. GitHub's default page is 30.
    """
    installations: list[dict[str, Any]] = []
    page = 1
    while page <= _MAX_INSTALLATION_PAGES:
        body = app_get(api_base_url, f"/app/installations?per_page=100&page={page}", jwt)
        batch = [i for i in body if isinstance(i, dict)] if isinstance(body, list) else []
        installations.extend(batch)
        if len(batch) < 100 or 'rel="next"' not in _LAST_LINK_HEADER[0]:
            break
        page += 1
    return installations


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
