"""Repository and dependency path resolution for the standalone model.

The model is intentionally a separate project from both the ISA
specification and any simulator.  This module is the single place where
those runtime inputs are located; callers should not infer paths from their
own source-file location.
"""

from __future__ import annotations

import os
from pathlib import Path


def repository_root() -> Path:
    """Return the checkout root containing ``pyproject.toml``."""

    return Path(__file__).resolve().parents[2]


def resolve_pto_spec(path: Path | str | None = None) -> Path:
    """Resolve the PTO specification checkout.

    Precedence is explicit CLI/API argument, ``PTO_SPEC_ROOT``, a pinned
    ``vendor/pto-spec`` checkout, then a conventional sibling checkout.  The
    final candidate is useful for local development but never references a
    simulator repository.
    """

    if path is not None:
        return Path(path).expanduser().resolve()
    configured = os.environ.get("PTO_SPEC_ROOT")
    if configured:
        return Path(configured).expanduser().resolve()
    root = repository_root()
    vendored = root / "vendor" / "pto-spec"
    if (vendored / ".git").exists() or (vendored / "build" / "pto-spec.asl").is_file():
        return vendored.resolve()
    return (root.parent / "pto-spec").resolve()


__all__ = ["repository_root", "resolve_pto_spec"]
