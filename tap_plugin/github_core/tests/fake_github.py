"""A fake GitHub REST client for the falsifiers' proof cases and the collector's proof run.

Answers ``get`` / ``get_paginated`` from a table of ``path -> payload`` (a payload that is an
``int`` is the HTTP status the path refuses with, raised as ``GithubAPIError``), and records
every path asked so a test can assert what was probed. Paths not in the table answer an
empty payload (``{}`` for ``get``, ``[]`` for a paginated walk) so the collector's many
optional surfaces read as "nothing there" rather than failing the run; a test that wants a
refusal on a surface names it. ``last_walk_complete`` is settable so a capped walk can be
staged (github-core#151).
"""

from __future__ import annotations

import base64
from typing import Any

from tap_plugin.github_core.collectors.github_collector.api_client import GithubAPIError


def contents_payload(text: str, *, path: str = "") -> dict[str, Any]:
    """A Contents API payload carrying ``text`` base64-encoded, as GitHub returns it."""
    return {
        "type": "file",
        "encoding": "base64",
        "path": path,
        "name": path.rsplit("/", 1)[-1],
        "content": base64.b64encode(text.encode("utf-8")).decode("ascii"),
    }


class FakeGithub:
    """A REST double: ``table[path]`` is the JSON answer, or an int status to refuse with."""

    def __init__(self, table: dict[str, Any] | None = None, *, walk_complete: bool = True) -> None:
        self.table: dict[str, Any] = dict(table or {})
        self.calls: list[str] = []
        self.walk_complete = walk_complete
        self.last_walk_complete = True
        self._next_link: str | None = None

    def answer(self, path: str, payload: Any) -> None:
        self.table[path] = payload

    def refuse(self, path: str, status: int) -> None:
        self.table[path] = status

    def get(self, path: str, *, params: dict[str, str] | None = None) -> Any:
        self.calls.append(path)
        payload = self.table.get(path, {})
        if isinstance(payload, int):
            raise GithubAPIError(status=payload, url=path, body="{}" if payload != 404 else '{"message": "Not Found"}')
        return payload

    def get_paginated(
        self,
        path: str,
        *,
        params: dict[str, str] | None = None,
        item_path: str | None = None,
        max_pages: int = 100,
    ) -> list[Any]:
        self.calls.append(path)
        payload = self.table.get(path, [] if item_path is None else {item_path: []})
        if isinstance(payload, int):
            raise GithubAPIError(status=payload, url=path, body="{}")
        self.last_walk_complete = self.walk_complete
        items = payload.get(item_path, []) if item_path else payload
        return list(items) if isinstance(items, list) else []


__all__ = ["FakeGithub", "contents_payload"]
