---
name: create-github-app
description: Create, install and verify the GitHub App that git-serious observes an account with — manifest-driven, least-privilege derived from the collection manifest, credential placed and proven end to end. Use when standing up a new git-serious instance, migrating an instance off a personal access token, or rotating an App's key. NOT for supplying a value a boot profile already declares (that is /provision-secrets).
allowed-tools: Read Write Edit Bash(python3 *) Bash(open *) Bash(scripts/dc *) Bash(curl *) Bash(chmod *) Bash(ls *) Bash(mkdir *) Glob Grep
argument-hint: <owner-login> [--public]
---

# Create the git-serious GitHub App

You are standing up the credential a git-serious instance observes an account with. Do this
manifest-first: the operator reviews a rendered permission table and presses one button, GitHub
creates the App **in their own account**, and hands **them** the private key. Nothing of ours
touches their data and there is no key of ours to steal.

> **This procedure is itself a security artifact.** git-serious exists partly because third-party
> App integrations are where organizations have repeatedly been compromised — over-broad grants,
> credentials nobody can enumerate, keys that outlive their purpose. Ours has to be the example.
> If a step here feels like it could be skipped, that is the step.

## Authoritative sources (read before improvising)

- **[`spec-github-core-v0.md`](../../../../specs/spec-github-core-v0.md)** — `req-github-core-secret`
  (the credential kinds this plugin owns) and `req-github-core-org-scope` (what a scope means).
- **[`github_collection_manifest.json`](../../collectors/github_collector/github_collection_manifest.json)**
  — every source declares the permission triple it needs. **This is where the App's permissions come
  from.** Never hand-write a permission list.
- **`tap_cares/specs/spec-tap-cares-secrets.md`** — envelope shape, scoping, the three leak surfaces.
- `/manage-secret` — run it if you are changing the *kind*, not just supplying a value.

## Why an App rather than a token

Worth being able to say out loud, because an operator will ask:

- A personal access token **is a person's power in token form** — it inherits their role, dies when
  they leave, and its ceiling is whatever they can do. An App is its own principal with its own
  declared permissions.
- The **private key never authenticates a request**. It signs a JWT valid for minutes, which is
  exchanged for an installation token that **expires in an hour** and is scoped to one installation.
- Two things git-serious needs are **App-only** and return `404` to any PAT: the organization's
  fine-grained-PAT grants, and the list of installed Apps. The credential-inventory view is
  unreachable without this.
- App rate limits are **per installation**; PAT limits are shared across everything that token
  touches. Multi-account observation gets cheaper, not more contended.

## Step 1 — Decide the shape, out loud

Confirm with the operator before rendering anything:

1. **Which account owns the App.** Prefer the organization over a personal account: an App owned by
   a person leaves with that person.
2. **Private or public.** Private (`--public` absent) means only this account may install it.
   Public is for distribution and is a different conversation — do not set it by default.
3. **Which account it will observe.** Usually the owner, but they can differ.
4. **Exploratory permissions, if any.** See Step 2. Default to none.

## Step 2 — Run it

One command does the whole flow. It runs **on the operator's machine, not in the container**, and
imports only the standard library — because the instance mounts its secrets root **read-only** and
cannot write its own credentials. That is the boundary this design exists to respect: *the operator
provisions, the instance consumes.*

```bash
cd <plugin>/tap_plugin/github_core/skills/create-github-app
python3 create_app.py --org <OWNER> \
  --instance-url "http://localhost:<WEB_PORT>/administrivia/cares" \
  --secrets-root "$HOME/tap-secrets" \
  [--observe <OTHER-ACCOUNT>] \
  [--exploratory organization:personal_access_tokens:read ...]
```

What it does, in order:

1. **Derives** the permission set as the union of the collection manifest's per-source triples —
   the same declaration the collector obeys, so the published claim cannot drift from the use.
2. **Claims an ephemeral port** on `127.0.0.1` and puts it in the manifest's `redirect_url`.
3. **Opens the review page** in the operator's browser and waits.
4. **Catches GitHub's redirect**, checking the `state` token; a mismatch is refused outright.
5. **Exchanges the one-time code** — the single moment the private key exists — writes the envelope
   at `0600`, and never prints the key.
6. **Bounces the browser to the running instance** and stops listening. Nothing is left running.

This is the flow `gh auth login` uses, and for the same reason: **GitHub offers no API for creating
an App.** A logged-in human must confirm in a browser, so a browser must be in the loop.

## Step 3 — The operator reviews the table

The page states the owner, every permission with its origin (`derived` or `EXPLORATORY`), that
there is no webhook and no event subscription, and whether the App is private or public.

**This review is the point of the whole flow.** Do not narrate it as a formality, and do not press
the button on the operator's behalf. Read the exploratory rows aloud and justify each one, or drop
it — a permission no collector uses is a permission that should not be granted yet.

## Step 4 — Install the App

Creating an App grants it nothing. Installation is the grant, and the command prints the URL:

```
https://github.com/apps/<slug>/installations/new
```

The operator chooses the account and the repository scope. **Prefer selected repositories over all
repositories** where the observed set is known — and note in passing that "all repositories" is
exactly the setting worth flagging on *other people's* Apps, so choose it deliberately or not at all.

## Step 5 — Wait, why did the browser land on a page that does not work yet?

Because the redirect fires at **creation**, before the credential is placed and before the App is
installed. Landing on the collectors page at that instant shows a collector that cannot run. That is
expected. The page becomes meaningful after Step 6.

## Step 6 — Verify end to end, before believing anything

```bash
scripts/dc exec -T web uv run python \
  /app/_dev-plugins/github_core/tap_plugin/github_core/skills/create-github-app/verify_app.py
```

This runs the whole chain the collector will run — key → JWT → installations → installation token →
one probe per reachable surface — and prints what the credential can and cannot see, including the
App-only endpoints and whether ruleset bypass actors are visible. Signing uses `cryptography`
directly against the system OpenSSL the FIPS posture validates, so App auth adds no crypto provider.

A permission that was granted but is unusable is worth discovering here rather than mid-collection.

## Step 7 — Record what was learned

The verification output answers questions that are otherwise expensive to establish: which endpoints
an App reaches that a token does not, whether `bypass_actors` is visible, and what the installation's
repository selection is. Write the answers where the next person will find them — the plugin's spec
or the product's docs — rather than leaving them in a terminal.

## Rotation and revocation

- **Rotate the key** by generating a new one in the App's settings and re-running Step 5's placement
  with the new PEM. Old keys keep working until deleted, so place first, then delete.
- **Revoke everything** by uninstalling the App from the account: every installation token dies with
  the installation, immediately. That is the advantage over a PAT, where revocation means finding
  the token.
- Installation tokens expire in an hour on their own. There is nothing long-lived to leak except the
  key in the envelope.

## Failure modes

- **`code` expired or reused** — it is single-use with a one-hour life. Re-run Step 2; a half-made
  App can be deleted from the account's settings.
- **The listener times out** — ten minutes with no redirect. Nothing was created on this machine;
  re-run. If the browser did not open, the review URL is printed — paste it.
- **State mismatch** — the redirect did not belong to this run. Refused by design; re-run rather
  than working around it.
- **Verification says NOT INSTALLED** — the App exists but Step 6 was skipped. Creating is not
  installing.
- **A probe returns 403 on a permission the table shows as granted** — the permission was added to
  the App *after* installation. Existing installations must accept new permissions; the operator
  gets a prompt in the account's settings.
- **Packages read as `unobservable` after re-accepting `organization_packages: read`** — expected
  until proven otherwise. GitHub's OpenAPI description marks every packages endpoint
  `enabledForGitHubApps: false`, and an App token was measured (2026-09-02) getting a 400 on the
  container listing. The collector records the surface as not observable rather than empty; a
  classic personal access token with `read:packages` is the credential GitHub documents for it
  (`req-github-core-packages`).
- **`404` on the PAT-grants endpoint** — either the `organization:personal_access_tokens:read`
  permission was not requested, or the observed account is a personal account, where the concept
  does not exist.
- **The instance still authenticates as a PAT** — the envelope was written to a different
  `TAP_SECRETS_ROOT` than the one the container mounts. Check the bind mount, not the file you just
  wrote.
