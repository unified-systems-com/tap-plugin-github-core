"""Workflow YAML parser.

Spec: plugins/github_core/specs/spec-github-core-v0.md
(req-github-core-workflow-parse). v0 extracts triggers, permissions, job
metadata (`runs-on`, `needs`, `uses`), and categorized refs for the link
manifest's enrichment phase. Secret/variable references are deferred per
req-github-core-backlog-references and deliberately NOT extracted here.
"""

from __future__ import annotations

import re
from typing import Any

import yaml

# Categorization patterns for the grid-link enrichment phase. Conservative —
# false positives produce zero-candidate warnings (no edge), not bad data.
_AWS_REGION_RE = re.compile(r"^(us|eu|ap|sa|ca|me|af|cn|us-gov)(-[a-z]+)+-\d+$")
_CLOUDFRONT_DISTRIBUTION_ID_RE = re.compile(r"^E[0-9A-Z]{12,16}$")
# Loose domain pattern: at least one dot, no slashes, no spaces, valid TLD.
_DOMAIN_NAME_RE = re.compile(
    r"^(?=.{1,253}$)[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?(?:\.[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?)+$", re.IGNORECASE
)


def parse_workflow_yaml(raw_yaml: str) -> dict[str, Any]:
    """Parse workflow YAML into the configuration shape declared in the spec.

    Returns a dict suitable for assignment to `github_workflow.configuration`.
    Keys: triggers, permissions, jobs (list), refs (categorized), raw_yaml,
    local_action_refs (list — `uses:` refs that point at local composite
    actions whose action.yml bodies v0 does not parse; see
    `req-github-core-workflow-parse-3`).
    """
    parsed = yaml.safe_load(raw_yaml) or {}
    if not isinstance(parsed, dict):
        return {
            "raw_yaml": raw_yaml,
            "triggers": [],
            "permissions": {},
            "jobs": [],
            "refs": _empty_refs(),
            "local_action_refs": [],
        }

    # YAML 1.1 gotcha: `on:` parses as boolean `True`. Check both keys.
    on_block = parsed.get("on", parsed.get(True))
    triggers = _extract_triggers(on_block)
    workflow_run = _extract_workflow_run(on_block)
    permissions = _normalize_permissions(parsed.get("permissions"))
    jobs = [_extract_job(job_id, job_def) for job_id, job_def in (parsed.get("jobs") or {}).items()]
    refs = _categorize_refs(parsed)
    local_action_refs = _detect_local_action_refs(jobs)

    return {
        "raw_yaml": raw_yaml,
        "triggers": triggers,
        "workflow_run": workflow_run,
        "permissions": permissions,
        "jobs": jobs,
        "refs": refs,
        "local_action_refs": local_action_refs,
    }


def _extract_workflow_run(on_block: Any) -> dict[str, Any] | None:
    """The `on: workflow_run:` block — which workflows' completion fires this one.

    Kept apart from the flat trigger list because it names OTHER workflows, which is the
    `TRIGGERS_WORKFLOW` input. Only the keys the author wrote are carried: GitHub defaults
    `types` to `[requested, completed]` when absent, and filling that in here would record a
    declaration the file does not make. Returns None when the workflow has no such trigger.
    """
    if not isinstance(on_block, dict) or "workflow_run" not in on_block:
        return None
    block = on_block.get("workflow_run")
    block = block if isinstance(block, dict) else {}
    out: dict[str, Any] = {"workflows": _string_list(block.get("workflows"))}
    for key in ("types", "branches", "branches-ignore"):
        if key in block:
            out[key.replace("-", "_")] = _string_list(block.get(key))
    return out


def _string_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value]
    if isinstance(value, list):
        return [str(x) for x in value]
    return [str(value)]


def _extract_triggers(on_block: Any) -> list[str]:
    if on_block is None:
        return []
    if isinstance(on_block, str):
        return [on_block]
    if isinstance(on_block, list):
        return [str(x) for x in on_block]
    if isinstance(on_block, dict):
        return sorted(on_block.keys())
    return []


def _normalize_permissions(perms: Any) -> dict[str, str] | str:
    # GitHub allows `permissions: read-all`/`write-all`/None, or a dict of
    # scope -> level. Preserve the shape for inspection.
    if perms is None:
        return {}
    if isinstance(perms, str):
        return perms
    if isinstance(perms, dict):
        return {k: str(v) for k, v in perms.items()}
    return {}


def _extract_job(job_id: str, job_def: Any) -> dict[str, Any]:
    """Extract one job's DECLARATION — the shape `workflow_job` nodes are built from.

    `runs_on` and `permissions` are deliberately not flattened to strings. `permissions` in
    particular keeps `None` when the job declares no block (it inherits) apart from `{}` when it
    declares an empty one (the job token gets nothing): collapsing the two would read the most
    locked-down job in a repository as the most permissive.
    """
    if not isinstance(job_def, dict):
        return {
            "id": job_id,
            "name": job_id,
            "runs_on": None,
            "permissions": None,
            "if": "",
            "environment": "",
            "needs": [],
            "uses": "",
            "workflow_call": None,
            "secrets_inherit": False,
            "secrets_passed": [],
            "checkout_ref": "",
            "cache_steps": [],
            "action_refs": [],
            "steps": [],
        }
    needs = job_def.get("needs") or []
    if isinstance(needs, str):
        needs = [needs]
    steps = job_def.get("steps") or []
    return {
        "id": job_id,
        "name": str(job_def.get("name") or job_id),
        "runs_on": _normalize_runs_on(job_def.get("runs-on")),
        # `permissions` absent -> None (inherits the workflow's); present -> the declared shape.
        "permissions": _normalize_permissions(job_def["permissions"]) if "permissions" in job_def else None,
        "if": str(job_def.get("if") or ""),
        "environment": _environment_name(job_def.get("environment")),
        "needs": [str(n) for n in needs],
        "uses": str(job_def.get("uses") or ""),
        # A job-level `uses:` is always a reusable-workflow call (an action cannot be used at
        # the job level), so the split is unconditional when the string is present.
        "workflow_call": split_workflow_call(str(job_def.get("uses") or "")),
        # `secrets: inherit` hands EVERY secret of the caller to the callee — the property that
        # turns a reusable-workflow call into the untrusted→privileged handoff. A mapping passes
        # named secrets; only the NAMES are kept (the values are `${{ secrets.X }}` expressions,
        # never material, but nothing here should ever be tempted to store one).
        "secrets_inherit": job_def.get("secrets") == "inherit",
        "secrets_passed": sorted(str(k) for k in job_def["secrets"]) if isinstance(job_def.get("secrets"), dict) else [],
        "checkout_ref": _checkout_ref(steps),
        "cache_steps": _cache_steps(steps),
        "action_refs": _action_refs(steps),
        "steps": steps,
    }


def _normalize_runs_on(runs_on: Any) -> list[str] | None:
    """Canonicalize `runs-on` to a list of labels, or None when the job declares none.

    GitHub accepts a bare string, a list of labels, or a `{group, labels}` mapping for runner
    groups. The list form is canonical here so a query for "which jobs run on a self-hosted
    label" is one shape rather than three.
    """
    if runs_on is None:
        return None
    if isinstance(runs_on, str):
        return [runs_on]
    if isinstance(runs_on, list):
        return [str(x) for x in runs_on]
    if isinstance(runs_on, dict):
        labels = runs_on.get("labels") or []
        if isinstance(labels, str):
            labels = [labels]
        out = [str(x) for x in labels]
        group = runs_on.get("group")
        if group:
            out.append(f"group:{group}")
        return out
    return [str(runs_on)]


def _environment_name(environment: Any) -> str:
    """The environment a job deploys to. Accepts the bare-string and `{name, url}` forms."""
    if isinstance(environment, str):
        return environment
    if isinstance(environment, dict):
        return str(environment.get("name") or "")
    return ""


def _checkout_ref(steps: Any) -> str:
    """The ref the job checks out, when it names one explicitly.

    An empty value means the job either does not check out or takes the default (the ref that
    triggered the run). A job triggered by `pull_request_target` that names
    `github.event.pull_request.head.sha` is running contributor code with the base repository's
    secrets — the most-cited shape in the incident corpus, and the reason this is a column rather
    than something to be dug out of a steps blob.
    """
    for step in steps if isinstance(steps, list) else []:
        if not isinstance(step, dict):
            continue
        uses = str(step.get("uses") or "")
        if not uses.startswith("actions/checkout"):
            continue
        with_block = step.get("with") or {}
        ref = with_block.get("ref") if isinstance(with_block, dict) else None
        if ref:
            return str(ref)
    return ""


#: `actions/cache` both restores and (at post-job) writes; the split actions do one each.
_CACHE_MODE_BY_ACTION: dict[str, str] = {
    "actions/cache/restore": "restore",
    "actions/cache/save": "write",
    "actions/cache": "restore_and_write",
}


def _cache_steps(steps: Any) -> list[dict[str, Any]]:
    """Declared cache usage: which step, which action, and the key EXPRESSION it uses.

    The key is kept as written (`${{ runner.os }}-node-${{ hashFiles('**/lock') }}`) and not
    resolved: evaluating it would mean implementing GitHub's expression language, and a guessed
    key that happens to be wrong would silently link a job to another job's cache entry. The
    concrete entries are collected separately as `actions_cache` nodes; the join between the two
    is a named gap (`req-github-core-caches`).
    """
    out: list[dict[str, Any]] = []
    for index, step in enumerate(steps if isinstance(steps, list) else []):
        if not isinstance(step, dict):
            continue
        uses = str(step.get("uses") or "")
        action = uses.split("@", 1)[0]
        mode = _CACHE_MODE_BY_ACTION.get(action)
        if mode is None:
            continue
        with_block = step.get("with") or {}
        with_block = with_block if isinstance(with_block, dict) else {}
        restore_keys = with_block.get("restore-keys") or ""
        out.append(
            {
                "step_index": index,
                "action": action,
                "mode": mode,
                "key_expression": str(with_block.get("key") or ""),
                "restore_keys": [k for k in str(restore_keys).splitlines() if k.strip()],
            }
        )
    return out


def _action_refs(steps: Any) -> list[dict[str, Any]]:
    """Every third-party action a job calls, with how it is pinned — the `USES_ACTION` input.

    Local `./` actions are excluded (they are the repository's own code, surfaced separately as
    `LOCAL_ACTION_DEFERRED`). Everything else — a repository action or a `docker://` image — is
    split into the path that identifies the action and the ref that pins it, because those are
    different facts: the path is a node, the pin is an edge property.
    """
    out: list[dict[str, Any]] = []
    for index, step in enumerate(steps if isinstance(steps, list) else []):
        if not isinstance(step, dict):
            continue
        uses = str(step.get("uses") or "")
        if not uses or uses.startswith("./"):
            continue
        out.append({"step_index": index, **split_uses(uses)})
    return out


_SHA_RE = re.compile(r"^[0-9a-f]{40}$")
#: An OCI digest is `sha256:` plus exactly 64 hex characters; anything else that starts with
#: `sha256:` is a malformed declaration, and a malformed pin must not read as an immutable one.
_DIGEST_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
_DOCKER_PREFIX = "docker://"

#: Immutable from the string alone (a commit SHA).
PIN_SHA = "sha"
#: Immutable from the string alone (an image digest).
PIN_DIGEST = "digest"
#: A mutable name RESOLVED to a tag against the action repository's refs.
PIN_TAG = "tag"
#: A mutable name RESOLVED to a branch against the action repository's refs.
PIN_BRANCH = "branch"
#: A mutable name whose kind the string cannot establish. Never guessed as `tag`.
PIN_UNRESOLVED = "unresolved"
#: No ref written at all.
PIN_UNPINNED = "unpinned"


def split_uses(uses: str) -> dict[str, Any]:
    """Split one `uses:` value into the action's identity and its declared pin.

    Returns ``{kind, action, action_path, owner, repository_full_name, subpath, ref, pin_kind}``.
    `action` duplicates `action_path` for the older readers of `configuration.action_refs`.

    `pin_kind` says only what the STRING proves. A 40-hex ref is a commit and a `sha256:` ref
    is a digest — both immutable, both `is_pinned`. A docker image's `:tag` is a tag by the
    registry's own vocabulary. A repository action's `@v4` or `@main` is a name the owner can
    repoint, and whether it is a tag or a branch is NOT visible here: the previous shape called
    every such name `tag`, which was a declaration that existed and was false. The collector
    upgrades `unresolved` to `tag`/`branch` only when it holds the action repository's refs.
    """
    if uses.startswith(_DOCKER_PREFIX):
        return _split_docker_uses(uses[len(_DOCKER_PREFIX) :])
    return _split_repository_uses(uses)


def _split_docker_uses(image: str) -> dict[str, Any]:
    """`docker://image[:tag|@sha256:digest]` — a registry pin, not a git one."""
    if "@" in image:
        path, _, ref = image.partition("@")
        pin = PIN_DIGEST if _DIGEST_RE.match(ref) else PIN_UNRESOLVED
    else:
        # A `:tag` after the last `/` (a registry may carry a port: `localhost:5000/img`).
        head, _, tail = image.rpartition("/")
        name, _, ref = tail.partition(":")
        path = f"{head}/{name}" if head else name
        pin = PIN_TAG if ref else PIN_UNPINNED
    action_path = f"{_DOCKER_PREFIX}{path}"
    return {
        "kind": "docker",
        "action": action_path,
        "action_path": action_path,
        "owner": "",
        "repository_full_name": "",
        "subpath": "",
        "ref": ref,
        "pin_kind": pin,
    }


def _split_repository_uses(uses: str) -> dict[str, Any]:
    """`owner/repo[/subdir]@ref` — the common form.

    Owner and repository are lower-cased: GitHub resolves them case-insensitively, so
    `Actions/Checkout` and `actions/checkout` are one repository and must be one node, or
    fan-in fragments and an exact-action query misses the differently cased declaration.
    The subpath stays as written — it is a filesystem path inside the repository.
    """
    path, _, ref = uses.partition("@")
    parts = path.split("/")
    has_repo = len(parts) >= 2
    repository_full_name = "/".join(part.lower() for part in parts[:2]) if has_repo else path.lower()
    subpath = "/".join(parts[2:])
    action_path = f"{repository_full_name}/{subpath}" if subpath else repository_full_name
    return {
        "kind": "repository",
        "action": action_path,
        "action_path": action_path,
        "owner": parts[0].lower() if has_repo else "",
        "repository_full_name": repository_full_name if has_repo else "",
        "subpath": subpath,
        "ref": ref,
        "pin_kind": _git_pin_kind(ref),
    }


def _git_pin_kind(ref: str) -> str:
    """What a git ref string proves on its own: a commit, nothing, or a name (unresolved)."""
    if not ref:
        return PIN_UNPINNED
    if _SHA_RE.match(ref):
        return PIN_SHA
    return PIN_UNRESOLVED


#: A same-repository reusable-workflow call (`./.github/workflows/x.yml`): no ref is written and
#: none can be — it runs at the caller's own commit, so it cannot be repointed independently.
PIN_LOCAL = "local"


def is_pinned(pin_kind: str) -> bool:
    """The one-bit answer every pinning control asks: immutable, or a name someone else keeps."""
    return pin_kind in (PIN_SHA, PIN_DIGEST, PIN_LOCAL)


def split_workflow_call(uses: str) -> dict[str, Any] | None:
    """Split a job-level `uses:` — a reusable-workflow call — into callee identity and pin.

    Two written forms (GitHub's `jobs.<job_id>.uses`): `./.github/workflows/x.yml` for a
    workflow in the same repository, which takes no ref and runs at the caller's commit; and
    `owner/repo/.github/workflows/x.yml@ref` for another repository's, which requires one.
    Returns None for an empty string. The pin grammar is `split_uses`'s: a SHA is `sha`, a
    name is `unresolved` until the collector can match it against in-scope refs.
    """
    if not uses:
        return None
    if uses.startswith("./"):
        return {
            "same_repository": True,
            "repository_full_name": "",
            "path": uses[2:],
            "ref": "",
            "pin_kind": PIN_LOCAL,
        }
    spec, _, ref = uses.partition("@")
    parts = spec.split("/")
    return {
        "same_repository": False,
        "repository_full_name": "/".join(parts[:2]) if len(parts) >= 2 else "",
        "path": "/".join(parts[2:]),
        "ref": ref,
        "pin_kind": _git_pin_kind(ref),
    }


def _detect_local_action_refs(jobs: list[dict[str, Any]]) -> list[dict[str, str]]:
    """Find `uses:` references that point at local composite actions.

    Local-action convention: `uses: ./path/to/action` (or `./.github/actions/foo`).
    v0 does not parse these action.yml bodies; the collector surfaces a
    structured warning per detected reference so an operator sees the
    deferred shape rather than silently missing it
    (`req-github-core-workflow-parse-3`).

    Reusable workflow calls — `uses: ./.github/workflows/x.yml` — are a
    different category and explicitly NOT flagged here: they end in `.yml` or
    `.yaml`, point at a workflow file (not an action directory), and have
    different runtime semantics.
    """
    refs: list[dict[str, str]] = []
    for job in jobs:
        job_id = job.get("id", "")
        for path, value in _walk_uses_paths(job):
            if not isinstance(value, str) or not value.startswith("./"):
                continue
            if value.endswith((".yml", ".yaml")):
                # Reusable workflow call — different category.
                continue
            refs.append({"job_id": job_id, "path": path, "uses": value})
    return refs


def _walk_uses_paths(job: dict[str, Any]) -> list[tuple[str, Any]]:
    """Yield (location, value) for every `uses:` field reachable inside a job."""
    out: list[tuple[str, Any]] = []
    if job.get("uses"):
        out.append(("job.uses", job["uses"]))
    steps = job.get("steps") or []
    for i, step in enumerate(steps):
        if isinstance(step, dict) and step.get("uses"):
            out.append((f"steps[{i}].uses", step["uses"]))
    return out


def _categorize_refs(parsed: dict[str, Any]) -> dict[str, list[str]]:
    """Walk the YAML for string values and categorize them for link rules.

    Used by the enrichment phase. Conservative pattern matching; false hits
    just fail to find a grid candidate and produce no edge.

    Known limitation: this shape-regex approach both fabricates (tags version
    pins / filenames as `domain_names`) and misses `${{ }}`-embedded values.
    The successor design — match against the known grid vocabulary instead of
    guessing shapes — is specced as `req-github-core-backlog-grid-vocab-links`.
    """
    refs = _empty_refs()
    for value in _string_values(parsed):
        if _AWS_REGION_RE.match(value):
            _append_unique(refs["aws_regions"], value)
        elif _CLOUDFRONT_DISTRIBUTION_ID_RE.match(value):
            _append_unique(refs["cloudfront_distribution_ids"], value)
        elif _DOMAIN_NAME_RE.match(value):
            _append_unique(refs["domain_names"], value.lower())
    return refs


def _empty_refs() -> dict[str, list[str]]:
    return {
        "domain_names": [],
        "aws_regions": [],
        "cloudfront_distribution_ids": [],
    }


def _append_unique(lst: list[str], value: str) -> None:
    if value not in lst:
        lst.append(value)


def _string_values(obj: Any) -> list[str]:
    out: list[str] = []
    stack: list[Any] = [obj]
    while stack:
        node = stack.pop()
        if isinstance(node, str):
            out.append(node)
        elif isinstance(node, dict):
            stack.extend(node.values())
        elif isinstance(node, list):
            stack.extend(node)
    return out
