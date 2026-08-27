"""Validation for the GitHub API base URL the host flow will send credentials to.

One derivation, two callers (`create_app.py`, `verify_app.py`) — the base URL arrives
from outside the program in both: a CLI flag on the way in, a credential envelope field
on the way back. Stdlib-only, because `create_app.py` runs on the operator's host with no
dependency set (req-github-core-app-auth-5).

Why this is a security control and not tidiness: the value is interpolated into a URL
handed to `urllib.request.urlopen`, which honours whatever scheme it is given. An
`http://` base sends the one-time manifest code — the single value that converts into the
App's private key — in cleartext to a host of the caller's choosing, and a `file://` base
turns an API call into a local file read. Both are cheap to refuse here and impossible to
retrofit once a key has left.
"""

from __future__ import annotations

from urllib.parse import urlsplit

__all__ = ["validate_api_base_url"]


def validate_api_base_url(value: str) -> str:
    """Return `value` without its trailing slash, or raise ValueError.

    Accepts `https://api.github.com` and a GitHub Enterprise Server base such as
    `https://ghe.example.com/api/v3`. Refuses anything else.
    """
    parts = urlsplit(value)
    if parts.scheme != "https":
        raise ValueError(
            f"--api-base-url must be https (got {parts.scheme or 'no'} scheme): {value!r}. "
            "The manifest code exchanged over this URL converts into the App's private key."
        )
    if not parts.hostname:
        raise ValueError(f"--api-base-url has no host: {value!r}")
    if parts.username or parts.password:
        raise ValueError("--api-base-url must not carry credentials in the URL")
    if parts.query or parts.fragment:
        raise ValueError(f"--api-base-url must not carry a query or fragment: {value!r}")
    return value.rstrip("/")
