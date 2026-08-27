"""Prove a placed github_app credential actually works, permission by permission.

Runs the full chain the collector will run — key -> JWT -> installation -> installation token ->
one probe per declared permission — and reports what the credential can and cannot see. A
permission that is granted but unusable is worth finding here rather than mid-collection.

Signing uses `cryptography` directly (already a dependency, built against the system OpenSSL that
the FIPS posture validates), so App auth introduces no new crypto provider.

Usage:  python verify_app.py [--secrets-root DIR]
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import time
import urllib.error
import urllib.request
from pathlib import Path

import api_url
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding


def _b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def mint_jwt(app_id: int | str, private_key_pem: str) -> str:
    """RS256 JWT, ~20 lines, no JWT library. Backdated 60s for clock skew; GitHub caps life at 10m."""
    now = int(time.time())
    header = _b64(json.dumps({"alg": "RS256", "typ": "JWT"}, separators=(",", ":")).encode())
    claims = _b64(json.dumps({"iat": now - 60, "exp": now + 540, "iss": str(app_id)},
                             separators=(",", ":")).encode())
    signing_input = f"{header}.{claims}".encode()
    key = serialization.load_pem_private_key(private_key_pem.encode(), password=None)
    signature = key.sign(signing_input, padding.PKCS1v15(), hashes.SHA256())
    return f"{header}.{claims}.{_b64(signature)}"


def call(url: str, token: str, *, scheme: str = "Bearer", method: str = "GET") -> tuple[int, object]:
    request = urllib.request.Request(
        url, method=method,
        headers={"Authorization": f"{scheme} {token}", "Accept": "application/vnd.github+json",
                 "User-Agent": "tap-github-core"},
    )
    try:
        # nosec B310 — every caller builds `url` from the validate_api_base_url()'d base.
        with urllib.request.urlopen(request, timeout=30) as response:  # nosec B310
            return response.status, json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read()[:160].decode("utf-8", "replace")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--secrets-root", type=Path,
                    default=Path(os.environ.get("TAP_SECRETS_ROOT", "/run/tap-secrets")))
    args = ap.parse_args()

    envelope = json.loads((args.secrets_root / "github_core" / "collector.secret.json").read_text())
    if envelope.get("kind") != "github_app":
        print(f"  credential kind is {envelope.get('kind')!r}, not github_app — nothing to verify")
        return 1
    d = envelope["data"]
    # Same control as create_app.py: the envelope is operator-written, but a mistyped or
    # tampered base URL here would carry a live installation token to it.
    try:
        api = api_url.validate_api_base_url(d.get("api_base_url") or "https://api.github.com")
    except ValueError as exc:
        print(f"  envelope api_base_url rejected: {exc}")
        return 1
    owner = d["owner"]

    print("  chain")
    jwt = mint_jwt(d["app_id"], d["private_key"])
    status, app = call(f"{api}/app", jwt)
    print(f"    JWT -> /app                         {status} {app.get('slug') if status == 200 else app}")
    if status != 200:
        return 1

    status, installs = call(f"{api}/app/installations", jwt)
    print(f"    JWT -> /app/installations           {status} {len(installs) if status == 200 else installs} installation(s)")
    if status != 200 or not installs:
        print("    NOT INSTALLED — install it, then re-run")
        return 1
    match = next((i for i in installs if i["account"]["login"].lower() == owner.lower()), installs[0])
    inst_id, sel = match["id"], match.get("repository_selection")
    print(f"    installation {inst_id} on {match['account']['login']} (repository_selection={sel})")

    request = urllib.request.Request(
        f"{api}/app/installations/{inst_id}/access_tokens", method="POST",
        headers={"Authorization": f"Bearer {jwt}", "Accept": "application/vnd.github+json",
                 "User-Agent": "tap-github-core"})
    # nosec B310 — `api` came from validate_api_base_url() above.
    with urllib.request.urlopen(request, timeout=30) as response:  # nosec B310
        token_payload = json.loads(response.read().decode("utf-8"))
    token = token_payload["token"]
    print(f"    installation token                  expires {token_payload['expires_at']}")
    print(f"    granted permissions                 {token_payload.get('permissions')}")

    print("\n  probes (what the credential can actually reach)")
    probes = [
        ("repos (metadata)", f"{api}/orgs/{owner}/repos?per_page=1"),
        ("installation repos", f"{api}/installation/repositories?per_page=1"),
        ("org installations", f"{api}/orgs/{owner}/installations"),
        ("PAT grants  [App-only]", f"{api}/orgs/{owner}/personal-access-tokens?per_page=1"),
        ("org members", f"{api}/orgs/{owner}/members?per_page=1"),
    ]
    for label, url in probes:
        status, body = call(url, token)
        n = len(body) if isinstance(body, list) else (body.get("total_count") if isinstance(body, dict) else "")
        print(f"    {label:<28} {status} {n if status == 200 else str(body)[:70]}")

    # The question the corpus flagged: is bypass visibility better, worse, or equal to a PAT?
    status, repos = call(f"{api}/orgs/{owner}/repos?per_page=1", token)
    if status == 200 and repos:
        full = repos[0]["full_name"]
        status, rulesets = call(f"{api}/repos/{full}/rulesets", token)
        print(f"    rulesets on {full:<16} {status} {len(rulesets) if status == 200 else rulesets}")
        if status == 200 and rulesets:
            status, detail = call(f"{api}/repos/{full}/rulesets/{rulesets[0]['id']}", token)
            seen = isinstance(detail, dict) and "bypass_actors" in detail
            print(f"    bypass_actors visible to the App    {status} {seen}"
                  f"{' -> ' + str(detail.get('bypass_actors')) if seen else ''}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
