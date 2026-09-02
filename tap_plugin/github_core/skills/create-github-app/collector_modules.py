"""Path-load a collector module from this checkout, without installing anything.

The host-side scripts in this directory run on the operator's machine from a bare checkout
(req-github-core-app-auth-5). What they must NOT do is carry their own copy of anything the
collector derives — the JWT (`app_jwt.py`) or the credential fold (`credential_shape.py`) — because
a second copy is how "verified" and "works" drift apart. So they load the collector's module by
path, and this is the one place that knows where the collector lives relative to the skill.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType

_COLLECTOR_DIR = Path(__file__).resolve().parents[2] / "collectors" / "github_collector"


def load(name: str) -> ModuleType:
    """Load `collectors/github_collector/<name>.py` and return the module object."""
    path = _COLLECTOR_DIR / f"{name}.py"
    spec = importlib.util.spec_from_file_location(f"github_core_{name}", path)
    if spec is None or spec.loader is None:  # pragma: no cover - a broken checkout, not a code path
        raise SystemExit(f"cannot load the collector module {name!r} from {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module
