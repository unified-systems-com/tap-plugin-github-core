"""Create, exchange and place the git-serious GitHub App — one command, no copy-paste.

**Runs on the HOST, not in the container, and is stdlib-only** (the same discipline as
`tap/git_invocation.py`). Both facts follow from one architectural boundary: the instance mounts
its secrets root **read-only** and can never write its own credentials. The operator provisions;
the instance consumes. So the step that receives a private key has to happen out here, as you,
with your filesystem permissions — and out here there is no dependency set to rely on.

The flow is the one `gh auth login` uses, because GitHub deliberately offers **no API for creating
an App**: a logged-in human must confirm in a browser.

    1. derive the manifest from the collection manifest (permissions are a union over sources)
    2. start a short-lived listener on 127.0.0.1:<ephemeral>
    3. open the review page — the operator reads the permission table and presses the button
    4. GitHub creates the App and redirects back to the listener with ?code=
    5. exchange the code (the ONE moment the private key exists), write the envelope 0600
    6. bounce the browser to the running instance, and stop listening

Nothing is left running. Nothing is written anywhere but the envelope.

Usage:
    python3 create_app.py --org <login> [--observe <login>] [--instance-url URL]
                          [--secrets-root DIR] [--name NAME] [--public]
                          [--exploratory surface:key:level ...]
"""

from __future__ import annotations

import argparse
import html
import json
import os
import secrets
import stat
import threading
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import api_url
import manifest as manifest_lib

_TIMEOUT_SECONDS = 600


def _page(body: str) -> bytes:
    return (
        "<!doctype html><meta charset='utf-8'><title>git-serious — GitHub App setup</title>"
        "<style>body{font:16px/1.6 -apple-system,system-ui,sans-serif;max-width:44rem;"
        "margin:3rem auto;padding:0 1.5rem;color:#1b1d21}"
        "table{border-collapse:collapse;width:100%;margin:1.2rem 0;font-size:.92em}"
        "td,th{border-bottom:1px solid #e2e0da;padding:.4rem .6rem;text-align:left}"
        "th{font-weight:600;color:#2e4a5c}code{background:#f3f1eb;padding:.1em .35em;border-radius:2px}"
        "button{font:inherit;padding:.65rem 1.3rem;cursor:pointer;border:1px solid #2e4a5c;"
        "background:#2e4a5c;color:#fff;border-radius:3px}"
        ".x{color:#8a2b22;font-weight:600}.ok{color:#3f6b52;font-weight:600}</style>" + body
    ).encode("utf-8")


class _Flow:
    """Shared state between the two requests the browser makes."""

    def __init__(self) -> None:
        self.state = secrets.token_urlsafe(24)
        self.code: str | None = None
        self.error: str | None = None
        self.done = threading.Event()


def _handler_factory(flow: _Flow, form_html: bytes, instance_url: str):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args) -> None:  # keep the operator's terminal clean
            return

        def do_GET(self) -> None:  # noqa: N802 - stdlib naming
            parsed = urllib.parse.urlparse(self.path)
            if parsed.path == "/":
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.end_headers()
                self.wfile.write(form_html)
                return
            if parsed.path != "/callback":
                self.send_response(404)
                self.end_headers()
                return

            params = urllib.parse.parse_qs(parsed.query)
            got_state = (params.get("state") or [""])[0]
            code = (params.get("code") or [""])[0]
            if got_state != flow.state:
                # The redirect did not belong to this request. Refuse it.
                flow.error = "state mismatch — the redirect did not belong to this request"
            elif not code:
                flow.error = "GitHub redirected without a code"
            else:
                flow.code = code

            if flow.error:
                self.send_response(400)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.end_headers()
                self.wfile.write(_page(f"<h1>Setup failed</h1><p class='x'>{html.escape(flow.error)}</p>"))
            else:
                # Hand the operator back to their instance; the exchange happens in the main
                # thread while the browser is already on its way.
                self.send_response(302)
                self.send_header("Location", instance_url)
                self.end_headers()
            flow.done.set()

    return Handler


def _render_form(mf: dict, derived_keys: set[str], org: str, port: int, state: str) -> bytes:
    rows = "".join(
        f"<tr><td><code>{html.escape(k)}</code></td><td>{html.escape(v)}</td>"
        f"<td>{'derived' if k in derived_keys else '<span class=x>EXPLORATORY</span>'}</td></tr>"
        for k, v in mf["default_permissions"].items()
    )
    action = f"https://github.com/organizations/{urllib.parse.quote(org)}/settings/apps/new?state={state}"
    access = "public — any account may install it" if mf["public"] else "private — this account only"
    return _page(
        f"<h1>Create the git-serious GitHub App</h1>"
        f"<p>This creates the App in <strong>{html.escape(org)}</strong>. The private key it generates is "
        f"handed to <em>you</em> and written to your own secret store — it is never sent anywhere else.</p>"
        f"<p><strong>Read the permissions before pressing the button.</strong> Rows marked "
        f"<em>derived</em> come from a declared collector source. Rows marked "
        f"<span class=x>EXPLORATORY</span> were requested on the command line and no collector uses "
        f"them yet.</p>"
        f"<table><tr><th>Permission</th><th>Level</th><th>Origin</th></tr>{rows}</table>"
        f"<p>Webhook: <strong>none</strong> · Events: <strong>none</strong> · Access: "
        f"<strong>{access}</strong></p>"
        f"<form action='{action}' method='post'>"
        f"<input type='hidden' name='manifest' value='{html.escape(json.dumps(mf), quote=True)}'>"
        f"<button type='submit'>Create GitHub App on GitHub &rarr;</button></form>"
        f"<p style='color:#777;font-size:.9em'>Listening on <code>127.0.0.1:{port}</code> for GitHub's "
        f"redirect. Close this window to cancel; nothing has been created yet.</p>"
    )


def convert(code: str, api_base_url: str) -> dict:
    """Exchange the one-time manifest code for the App's credentials."""
    request = urllib.request.Request(
        f"{api_base_url.rstrip('/')}/app-manifests/{code}/conversions",
        method="POST",
        headers={"Accept": "application/vnd.github+json", "User-Agent": "tap-github-core"},
    )
    # nosec B310 — api_base_url passed validate_api_base_url() at argparse time.
    with urllib.request.urlopen(request, timeout=30) as response:  # nosec B310
        return json.loads(response.read().decode("utf-8"))


def place_envelope(data: dict, *, owner: str, api_base_url: str, secrets_root: Path) -> Path:
    """Write the App credential into the envelope, 0600, without ever printing the key.

    **Merges into the App slot; never overwrites the file.** An envelope may carry a personal
    access token alongside the App, because neither credential dominates: only an App sees the
    account's installed-App inventory and its fine-grained PAT grants, and only an owner-minted
    PAT sees a ruleset's bypass actors. Standing up an App must therefore not silently destroy the
    token sitting beside it — the operator would discover it as a permanently blank column rather
    than as an error, which is the worst way to lose a credential.

    A pre-existing envelope of the older single-credential kinds is read for anything worth
    keeping and still moved aside, since its shape is not the one we write.
    """
    app_block = {
        "app_id": data["id"],
        "app_slug": data["slug"],
        "private_key": data["pem"],
    }
    target = secrets_root / "github_core" / "collector.secret.json"
    target.parent.mkdir(parents=True, exist_ok=True)

    existing: dict = {}
    if target.exists():
        try:
            existing = json.loads(target.read_text())
        except (OSError, ValueError):
            existing = {}
        superseded = target.with_suffix(".json.superseded")
        target.replace(superseded)
        print(f"  previous credential copied aside -> {superseded}")
        print("  delete it once verification passes")

    existing_data = existing.get("data") if isinstance(existing.get("data"), dict) else {}
    carried = {}
    # Carry a token forward whichever shape it arrived in: the combined envelope's `pat` block,
    # or a legacy `github_pat` envelope's bare `token`.
    if isinstance(existing_data.get("pat"), dict):
        carried["pat"] = existing_data["pat"]
    elif existing.get("kind") == "github_pat" and existing_data.get("token"):
        carried["pat"] = {k: v for k, v in existing_data.items() if k in {"token"}}
    if carried:
        print("  carried the existing personal access token forward into this envelope")

    envelope = {
        "scope": "github_core",
        "key": "collector",
        "kind": "github",
        "description": (
            f"git-serious credentials observing {owner}. Read-only. GitHub App #{data['id']} "
            f"({data['slug']}): the private key signs a short-lived JWT exchanged for an "
            "installation token that expires in one hour, and the key never authenticates a "
            "request directly. A personal access token may ride alongside it — neither credential "
            "can see everything the other can."
        ),
        "data": {
            "owner": owner,
            "api_base_url": api_base_url,
            **carried,
            "app": app_block,
        },
    }
    target.write_text(json.dumps(envelope, indent=2) + "\n")
    target.chmod(stat.S_IRUSR | stat.S_IWUSR)
    return target


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--org", required=True, help="account that will OWN the App")
    ap.add_argument("--observe", help="account the instance will observe (default: --org)")
    ap.add_argument("--name", default="git-serious")
    ap.add_argument("--public", action="store_true",
                    help="allow other accounts to install it — NOT the per-instance model")
    ap.add_argument("--exploratory", nargs="*", default=[], metavar="surface:key:level")
    ap.add_argument("--api-base-url", default="https://api.github.com")
    ap.add_argument("--instance-url", default="http://localhost:8010/administrivia/cares",
                    help="where to send the browser once the credential is placed")
    ap.add_argument("--secrets-root", type=Path,
                    default=Path(os.environ.get("TAP_SECRETS_ROOT", Path.home() / "tap-secrets")))
    args = ap.parse_args()
    # Refuse a non-https base URL before the browser opens or a socket binds. The manifest
    # code exchanged over this URL converts into the App private key exactly once; an http://
    # or file:// base would leak or divert it (SonarCloud SSRF finding, PR #3).
    try:
        args.api_base_url = api_url.validate_api_base_url(args.api_base_url)
    except ValueError as exc:
        ap.error(str(exc))
    observe = args.observe or args.org

    flow = _Flow()
    # Claim an ephemeral port first: the manifest must name the real redirect_url before the
    # browser ever sees it, so the port has to be known before the handler exists.
    probe = HTTPServer(("127.0.0.1", 0), BaseHTTPRequestHandler)
    port = probe.server_address[1]
    probe.server_close()

    redirect_url = f"http://127.0.0.1:{port}/callback"
    mf = manifest_lib.build(org=args.org, redirect_url=redirect_url, name=args.name,
                            public=args.public, exploratory=args.exploratory)
    repo_perms, org_perms = manifest_lib.derive_permissions()
    derived = {manifest_lib._manifest_key("repository", k) for k in repo_perms} | {
        manifest_lib._manifest_key("organization", k) for k in org_perms
    }

    print(f"  permissions ({len(mf['default_permissions'])}):")
    for key, level in mf["default_permissions"].items():
        print(f"    {key:<40} {level}   {'derived' if key in derived else 'EXPLORATORY'}")
    print(f"  owner       {args.org}\n  observes    {observe}\n  access      "
          f"{'public' if args.public else 'private'}\n  webhook     none")

    form = _render_form(mf, derived, args.org, port, flow.state)
    httpd = HTTPServer(("127.0.0.1", port), _handler_factory(flow, form, args.instance_url))
    threading.Thread(target=httpd.serve_forever, daemon=True).start()

    review_url = f"http://127.0.0.1:{port}/"
    print(f"\n  review page {review_url}  (opening in your browser)")
    webbrowser.open(review_url)

    if not flow.done.wait(timeout=_TIMEOUT_SECONDS):
        httpd.shutdown()
        print("\n  timed out waiting for GitHub's redirect — nothing was created on this machine")
        return 1
    httpd.shutdown()

    if flow.error or not flow.code:
        print(f"\n  setup failed: {flow.error}")
        return 1

    print("\n  exchanging the one-time code ...")
    try:
        data = convert(flow.code, args.api_base_url)
    except urllib.error.HTTPError as exc:
        print(f"  exchange failed: HTTP {exc.code} {exc.read()[:200]!r}")
        return 1
    target = place_envelope(data, owner=observe, api_base_url=args.api_base_url,
                            secrets_root=args.secrets_root)
    print(f"  App id      {data['id']}  ({data['slug']})")
    print("  private key written to the envelope, never printed")
    print(f"  envelope    {target}  (0600)")
    print(f"\n  NEXT: install it -> https://github.com/apps/{data['slug']}/installations/new")
    print("        then verify   -> verify_app.py (in the container)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
