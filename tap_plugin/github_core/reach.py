"""What this credential can PROVE it could look at — the reach statement (github-core#157).

A 404 from GitHub carries two different facts under one status line: the object is gone, or
the object is there and this credential may not see it. The probe cannot tell them apart from
the response, so it judges the 404 against the credential's **reach**: the set of repositories
the credential can demonstrably reach right now. Inside the reach a 404 means gone; outside it,
or where the reach cannot be observed at all, the answer is ``UNDETERMINED(scope_unknown)`` —
a credential that could not prove it could look never reads as gone
(``req-grid-reconcile-absence-states``).

Ruled by George on 2026-09-20 (option D) against ``tap-plugin-github-core#155``.

**What is observable, per credential kind.** Only the App path is built here; the rest is named
rather than guessed, because a reach nobody can observe must read as *not observable* and not as
*empty*:

- **GitHub App** — observable. ``repository_selection`` says ``all`` (the installation follows
  the account into every repository it owns) or ``selected`` (an explicit list), and
  ``GET /installation/repositories`` walks that list. This module.
- **Classic token** — observable from the token's own ``X-OAuth-Scopes`` response header, which
  the API client does not surface yet. github-core#158.
- **Org-owned fine-grained token** — observable only from the ORG side, and only by an App
  (``GET /orgs/{org}/personal-access-tokens``). github-core#159.
- **User-owned fine-grained token** — NOT observable, permanently: a fine-grained token cannot
  introspect itself (no ``X-OAuth-Scopes``; ``X-Accepted-GitHub-Permissions`` names what an
  endpoint requires, not what the token holds), and with no organization there is no org-side
  listing to read. Every 404 such a credential receives stays undetermined, by design.

The permission map narrows independently of the repository set — an installation can keep every
repository and lose ``actions:read`` — and that judgement is github-core#160, not this module.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from tap_plugin.github_core.collectors.github_collector.api_client import GithubAPIError

logger = logging.getLogger(__name__)

#: The installation follows the account: every repository the account owns is in reach.
SELECTION_ALL = "all"
#: An explicit list of repositories; membership is the question.
SELECTION_SELECTED = "selected"
#: The reach could not be observed. NOT "the reach is empty" — three states, never two.
SELECTION_UNKNOWN = "unknown"

#: Why a PAT's reach is not observable here, in the operator's terms. Named rather than silent:
#: an unobservable reach is a fact about the credential, and the record says which one.
_PAT_NOTE = (
    "a personal access token cannot introspect its own reach: a classic token's scopes ride the "
    "X-OAuth-Scopes header the API client does not surface yet (github-core#158), and a "
    "fine-grained token has no self-introspection at all — its reach is observable only from the "
    "organization side, by an App (github-core#159)"
)

_NO_CREDENTIAL_NOTE = "the envelope holds neither an App nor a token, so nothing can be reached"


@dataclass(frozen=True)
class Reach:
    """What one run's credential can prove it may look at.

    ``repository_ids`` and ``repository_names`` are ``None`` when unobserved — never an empty
    frozenset standing in for could-not-look, which is the whole distinction this type exists
    to keep.
    """

    #: Which credential the statement describes: ``app``, ``pat`` or ``none``.
    credential: str
    #: ``all``, ``selected`` or ``unknown`` — the installation's ``repository_selection``.
    selection: str
    #: The account login the installation is ON, lower-cased. ``all`` means "every repository of
    #: THIS account", never "every repository on GitHub", so without the account the ``all``
    #: branch has no boundary to test and answers *cannot say* (found in review, PR# 161).
    account: str | None = None
    #: GitHub's numeric ids the installation listed about itself; None when unobserved.
    repository_ids: frozenset[int] | None = None
    #: The same repositories as lower-cased ``owner/name``; None when unobserved.
    repository_names: frozenset[str] | None = None
    #: Why the reach is what it is, for the human reading the verdict later.
    note: str = ""

    @property
    def observable(self) -> bool:
        """Whether this credential's reach was observed at all."""
        return self.selection in (SELECTION_ALL, SELECTION_SELECTED)

    def holds_repository(self, *, full_name: str = "", github_id: Any = None) -> bool | None:
        """Whether this repository is provably in reach. ``None`` means *cannot say*.

        ``None`` and ``False`` are both refusals to let a 404 stand, but they are different
        facts — "we could not look at the reach" versus "the reach does not contain it" — and
        the caller writes a different note for each.
        """
        if not self.observable:
            return None
        if self.selection == SELECTION_ALL:
            # The installation follows THIS account into every repository it owns — which is a
            # statement about one account, not about GitHub. A repository belonging to anyone
            # else is outside the installation entirely and 404s for that reason, so the owner
            # is compared before the 404 is allowed to mean anything.
            if not self.account:
                return None
            owner = full_name.split("/", 1)[0].lower() if "/" in full_name else ""
            if not owner:
                return None
            return owner == self.account
        if github_id is not None and self.repository_ids is not None:
            try:
                return int(github_id) in self.repository_ids
            except (TypeError, ValueError):
                pass
        if full_name and self.repository_names is not None:
            return full_name.lower() in self.repository_names
        # `selected` with nothing to compare against is not a membership answer.
        return None


#: A reach that says only "not observed", with the reason attached.
def unobservable(credential: str, note: str) -> Reach:
    """The reach of a credential whose grants cannot be read."""
    return Reach(credential=credential, selection=SELECTION_UNKNOWN, note=note)


def _walk_installation_repositories(client: Any) -> tuple[frozenset[int], frozenset[str]] | None:
    """Every repository the installation token lists about itself, or None when the walk failed.

    A refused or broken walk yields None — an incomplete list would answer "not in reach" for
    repositories that are, which is the failure this whole module exists to prevent.
    """
    try:
        items = client.get_paginated("/installation/repositories", item_path="repositories")
    except GithubAPIError as exc:
        logger.warning("[4c1d] installation repository walk refused (HTTP %s); reach not observable", exc.status)
        return None
    except Exception:  # noqa: BLE001 — an unobservable reach is an answer, not a crash
        logger.warning("[b90f] installation repository walk failed; reach not observable")
        return None
    ids: set[int] = set()
    names: set[str] = set()
    for item in items or []:
        if not isinstance(item, dict):
            continue
        raw_id = item.get("id")
        if raw_id is not None:
            try:
                ids.add(int(raw_id))
            except (TypeError, ValueError):
                pass
        full_name = str(item.get("full_name") or "")
        if full_name:
            names.add(full_name.lower())
    return frozenset(ids), frozenset(names)


def resolve_reach(auth: Any, client: Any) -> Reach:
    """This credential's reach, observed now.

    ``auth`` is the resolved ``GithubAuth``; ``client`` must already be authenticated as the
    credential whose reach is being asked about — for the App path that means the installation
    token, which is what the falsifier's own client carries.
    """
    if getattr(auth, "has_app", False):
        # `installation` is populated when the installation token is minted; the falsifier's
        # client has already done that, so this reads the record rather than re-minting.
        installation = getattr(auth, "installation", None) or {}
        selection = str(installation.get("repository_selection") or "")
        account = str((installation.get("account") or {}).get("login") or "").lower() or None
        if selection == SELECTION_ALL:
            if account is None:
                return unobservable(
                    "app",
                    "the installation reports `all` but names no account, so the boundary that "
                    "`all` is relative to was not observed",
                )
            return Reach(
                credential="app",
                selection=SELECTION_ALL,
                account=account,
                note=f"the installation follows {account} into every repository it owns",
            )
        walked = _walk_installation_repositories(client)
        if walked is None:
            return unobservable(
                "app", "the installation's repository listing was refused, so its reach is not observed"
            )
        ids, names = walked
        return Reach(
            credential="app",
            selection=SELECTION_SELECTED,
            account=account,
            repository_ids=ids,
            repository_names=names,
            note=f"the installation names {len(names)} repository/repositories",
        )
    if getattr(auth, "has_pat", False):
        return unobservable("pat", _PAT_NOTE)
    return unobservable("none", _NO_CREDENTIAL_NOTE)


__all__ = [
    "SELECTION_ALL",
    "SELECTION_SELECTED",
    "SELECTION_UNKNOWN",
    "Reach",
    "resolve_reach",
    "unobservable",
]
