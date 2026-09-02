"""ASL-backed functional model for PTO instruction validation."""

from .runner import ElfError, ElfImage, LoadSegment, RunConfiguration, run

from .state import (
    ArchitectureState,
    BlockState,
    FaultState,
    MemoryRegion,
    MemoryState,
    ScalarState,
    SharedState,
    SharedTile,
    StateEnvelope,
    TileDescriptor,
    TileState,
    TileValue,
    canonical_hash,
    canonical_json,
    state_diff,
)
from .paths import repository_root, resolve_pto_spec

__all__ = [
    "ElfError", "ElfImage", "LoadSegment", "RunConfiguration", "run",
    "ArchitectureState",
    "BlockState",
    "FaultState",
    "MemoryRegion",
    "MemoryState",
    "ScalarState",
    "SharedState",
    "SharedTile",
    "StateEnvelope",
    "TileDescriptor",
    "TileState",
    "TileValue",
    "canonical_hash",
    "canonical_json",
    "state_diff",
    "repository_root", "resolve_pto_spec",
]
