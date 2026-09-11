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


def cache_root(path: Path | str | None = None) -> Path:
    """Return the model-owned cache directory, creating no files."""

    if path is not None:
        return Path(path).expanduser().resolve()
    configured = os.environ.get("ASL_MODEL_CACHE_ROOT")
    if configured:
        return Path(configured).expanduser().resolve()
    return repository_root() / ".cache"


def runtime_environment() -> dict[str, str]:
    """Return an environment with optional user-local ASLRef tooling."""

    environment = os.environ.copy()
    local_bin = Path.home() / ".local" / "bin"
    environment["PATH"] = str(local_bin) + os.pathsep + environment.get("PATH", "")
    # Keep compatibility with the existing PTO ASLRef setup while allowing a
    # caller to provide a different OPAM root in the environment.
    opam_root = os.environ.get("ASL_MODEL_OPAM_ROOT")
    if opam_root:
        environment.setdefault("OPAMROOT", str(Path(opam_root).expanduser()))
    else:
        conventional = Path.home() / ".opam-pto"
        if conventional.is_dir():
            environment.setdefault("OPAMROOT", str(conventional))
    return environment


__all__ = ["cache_root", "repository_root", "resolve_pto_spec", "runtime_environment"]
