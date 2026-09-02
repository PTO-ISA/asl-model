"""Versioned, passive architectural-state serialization objects.

These classes validate and serialize observed architectural data. They do not
initialize runner state, authorize memory access, map storage, reset execution,
or restore a live model. In particular, ``ArchitectureState.memory`` is only a
DTO representation of architectural memory data; the canonical runner and PTO
ASL remain the sole live storage and access authority. ``extensions`` is the
explicit additive channel for vendor- or instruction-family-specific fields.
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
import math
import re
from dataclasses import dataclass, field
from typing import Any, Mapping


SCHEMA_ID = "pto.asl-model.arch-state.v1"
SCHEMA_VERSION = 1


JsonValue = Any

_CANONICAL_MEMORY_ADDRESS = re.compile(r"^0x(?:0|[1-9a-f][0-9a-f]*)$")
_CANONICAL_MEMORY_PERMISSIONS = frozenset(
    {"r", "w", "x", "rw", "rx", "wx", "rwx"}
)


def _require_object(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be an object")
    if any(not isinstance(key, str) for key in value):
        raise ValueError(f"{label} keys must be text")
    return value


def _require_exact_object(
    value: Any, label: str, fields: tuple[str, ...]
) -> dict[str, Any]:
    row = _require_object(value, label)
    missing = [name for name in fields if name not in row]
    if missing:
        raise ValueError(f"{label} is missing required fields: " + ", ".join(missing))
    unexpected = sorted(set(row) - set(fields))
    if unexpected:
        raise ValueError(f"{label} has unexpected fields: " + ", ".join(unexpected))
    return row


def _require_list(value: Any, label: str) -> list[Any]:
    if not isinstance(value, list):
        raise ValueError(f"{label} must be an array")
    return value


def _require_string(value: Any, label: str) -> None:
    if not isinstance(value, str):
        raise ValueError(f"{label} must be text")


def _require_integer(value: Any, label: str, *, minimum: int | None = None) -> None:
    if not isinstance(value, int) or isinstance(value, bool):
        raise ValueError(f"{label} must be an integer")
    if minimum is not None and value < minimum:
        raise ValueError(f"{label} must be at least {minimum}")


def _require_optional_integer(value: Any, label: str) -> None:
    if value is not None:
        _require_integer(value, label)


def _require_boolean(value: Any, label: str) -> None:
    if not isinstance(value, bool):
        raise ValueError(f"{label} must be boolean")


def _require_json_object(value: Any, label: str) -> None:
    _require_object(value, label)
    canonical_json(value)


def _json_value(value: Any) -> JsonValue:
    """Convert supported values to JSON values and reject ambiguous values."""

    if dataclasses.is_dataclass(value):
        return {key: _json_value(item) for key, item in dataclasses.asdict(value).items()}
    if isinstance(value, Mapping):
        if any(not isinstance(key, str) for key in value):
            raise ValueError("mapping keys must be text")
        return {key: _json_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_value(item) for item in value]
    if isinstance(value, (str, int, bool)) or value is None:
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("canonical state cannot contain NaN or infinity")
        return value
    raise TypeError(f"value is not JSON serializable: {type(value).__name__}")


def canonical_json(value: Any) -> str:
    """Return deterministic JSON used for snapshots, cache keys and diffs."""

    return json.dumps(
        _json_value(value),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def canonical_hash(value: Any) -> str:
    """Return the SHA-256 of :func:`canonical_json`."""

    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


@dataclass
class ScalarState:
    """Scalar architectural state for one processing element/thread."""

    registers: dict[str, int] = field(default_factory=dict)
    pc: int = 0
    tpc: int = 0
    flags: dict[str, int | bool] = field(default_factory=dict)
    mode: str = ""
    extensions: dict[str, JsonValue] = field(default_factory=dict)

    def __post_init__(self) -> None:
        registers = _require_object(self.registers, "scalar.registers")
        for name, value in registers.items():
            _require_integer(value, f"scalar.registers[{name!r}]")
        _require_integer(self.pc, "scalar.pc")
        _require_integer(self.tpc, "scalar.tpc")
        flags = _require_object(self.flags, "scalar.flags")
        for name, value in flags.items():
            if not isinstance(value, (bool, int)):
                raise ValueError(f"scalar.flags[{name!r}] must be boolean or integer")
        _require_string(self.mode, "scalar.mode")
        _require_json_object(self.extensions, "scalar.extensions")


@dataclass
class BlockState:
    """State of the currently collected/executing Block transaction."""

    active: bool = False
    block_id: int | None = None
    start_pc: int | None = None
    instruction_count: int = 0
    attributes: dict[str, JsonValue] = field(default_factory=dict)
    extensions: dict[str, JsonValue] = field(default_factory=dict)

    def __post_init__(self) -> None:
        _require_boolean(self.active, "block.active")
        _require_optional_integer(self.block_id, "block.block_id")
        _require_optional_integer(self.start_pc, "block.start_pc")
        _require_integer(self.instruction_count, "block.instruction_count", minimum=0)
        _require_json_object(self.attributes, "block.attributes")
        _require_json_object(self.extensions, "block.extensions")


@dataclass
class TileDescriptor:
    """Descriptor needed to interpret a Tile payload."""

    dtype: str = ""
    layout: str = ""
    shape: list[int] = field(default_factory=list)
    valid_shape: list[int] = field(default_factory=list)
    strides: list[int] = field(default_factory=list)
    extensions: dict[str, JsonValue] = field(default_factory=dict)

    def __post_init__(self) -> None:
        _require_string(self.dtype, "descriptor.dtype")
        _require_string(self.layout, "descriptor.layout")
        for label, values, minimum in (
            ("descriptor.shape", self.shape, 0),
            ("descriptor.valid_shape", self.valid_shape, 0),
            ("descriptor.strides", self.strides, None),
        ):
            for index, value in enumerate(_require_list(values, label)):
                _require_integer(value, f"{label}[{index}]", minimum=minimum)
        _require_json_object(self.extensions, "descriptor.extensions")


@dataclass
class TileValue:
    """A Tile register value, including definedness and descriptor."""

    descriptor: TileDescriptor = field(default_factory=TileDescriptor)
    data: list[JsonValue] = field(default_factory=list)
    defined: list[bool] = field(default_factory=list)
    generation: int = 0
    extensions: dict[str, JsonValue] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not isinstance(self.descriptor, TileDescriptor):
            raise ValueError("tile value descriptor must be TileDescriptor")
        _require_list(self.data, "tile value data")
        canonical_json(self.data)
        for index, value in enumerate(_require_list(self.defined, "tile value defined")):
            _require_boolean(value, f"tile value defined[{index}]")
        _require_integer(self.generation, "tile value generation", minimum=0)
        _require_json_object(self.extensions, "tile value extensions")


@dataclass
class TileState:
    registers: dict[str, TileValue] = field(default_factory=dict)
    extensions: dict[str, JsonValue] = field(default_factory=dict)

    def __post_init__(self) -> None:
        registers = _require_object(self.registers, "tile.registers")
        if any(not isinstance(value, TileValue) for value in registers.values()):
            raise ValueError("tile.registers values must be TileValue")
        _require_json_object(self.extensions, "tile.extensions")


@dataclass
class SharedTile:
    """One aggregate Shared Tile and its publication generation."""

    descriptor: TileDescriptor = field(default_factory=TileDescriptor)
    data: list[JsonValue] = field(default_factory=list)
    defined: list[bool] = field(default_factory=list)
    generation: int = 0
    allocation_mask: int = 0
    extensions: dict[str, JsonValue] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not isinstance(self.descriptor, TileDescriptor):
            raise ValueError("shared tile descriptor must be TileDescriptor")
        _require_list(self.data, "shared tile data")
        canonical_json(self.data)
        for index, value in enumerate(_require_list(self.defined, "shared tile defined")):
            _require_boolean(value, f"shared tile defined[{index}]")
        _require_integer(self.generation, "shared tile generation", minimum=0)
        _require_integer(self.allocation_mask, "shared tile allocation_mask", minimum=0)
        _require_json_object(self.extensions, "shared tile extensions")


@dataclass
class SharedState:
    tiles: dict[str, SharedTile] = field(default_factory=dict)
    generations: dict[str, int] = field(default_factory=dict)
    extensions: dict[str, JsonValue] = field(default_factory=dict)

    def __post_init__(self) -> None:
        tiles = _require_object(self.tiles, "shared.tiles")
        if any(not isinstance(value, SharedTile) for value in tiles.values()):
            raise ValueError("shared.tiles values must be SharedTile")
        generations = _require_object(self.generations, "shared.generations")
        for name, value in generations.items():
            _require_integer(value, f"shared.generations[{name!r}]", minimum=0)
        _require_json_object(self.extensions, "shared.extensions")


@dataclass
class MemoryRegion:
    """Passive serialization metadata for an observed memory region."""

    base: int
    size: int
    permissions: str = "rw"
    name: str = ""
    extensions: dict[str, JsonValue] = field(default_factory=dict)

    def __post_init__(self) -> None:
        _require_integer(self.base, "memory region base", minimum=0)
        _require_integer(self.size, "memory region size", minimum=1)
        if (
            not isinstance(self.permissions, str)
            or self.permissions not in _CANONICAL_MEMORY_PERMISSIONS
        ):
            raise ValueError("memory region permissions must be a canonical rwx subset")
        _require_string(self.name, "memory region name")
        _require_json_object(self.extensions, "memory region extensions")


@dataclass
class MemoryState:
    """Passive serialized memory observations, never live runner storage."""

    cells: dict[str, int] = field(default_factory=dict)
    regions: list[MemoryRegion] = field(default_factory=list)
    extensions: dict[str, JsonValue] = field(default_factory=dict)

    def __post_init__(self) -> None:
        cells = _require_object(self.cells, "memory.cells")
        for address, byte in cells.items():
            if _CANONICAL_MEMORY_ADDRESS.fullmatch(address) is None:
                raise ValueError(f"invalid canonical memory cell address: {address!r}")
            if not isinstance(byte, int) or isinstance(byte, bool) or not 0 <= byte <= 255:
                raise ValueError("memory cells require byte values")
        regions = _require_list(self.regions, "memory.regions")
        if any(not isinstance(region, MemoryRegion) for region in regions):
            raise ValueError("memory regions must contain MemoryRegion values")
        ordered = sorted(self.regions, key=lambda region: region.base)
        if any(left.base + left.size > right.base for left, right in zip(ordered, ordered[1:])):
            raise ValueError("memory regions overlap")
        _require_json_object(self.extensions, "memory.extensions")


@dataclass
class FaultState:
    pending: bool = False
    kind: str = ""
    code: str = ""
    address: int | None = None
    instruction: str = ""
    message: str = ""
    recoverable: bool = False
    extensions: dict[str, JsonValue] = field(default_factory=dict)

    def __post_init__(self) -> None:
        _require_boolean(self.pending, "fault.pending")
        _require_string(self.kind, "fault.kind")
        _require_string(self.code, "fault.code")
        _require_optional_integer(self.address, "fault.address")
        _require_string(self.instruction, "fault.instruction")
        _require_string(self.message, "fault.message")
        _require_boolean(self.recoverable, "fault.recoverable")
        _require_json_object(self.extensions, "fault.extensions")


@dataclass
class ArchitectureState:
    """Complete passive architectural-state serialization data."""

    scalar: ScalarState = field(default_factory=ScalarState)
    block: BlockState = field(default_factory=BlockState)
    tile: TileState = field(default_factory=TileState)
    shared: SharedState = field(default_factory=SharedState)
    memory: MemoryState = field(default_factory=MemoryState)
    fault: FaultState = field(default_factory=FaultState)
    pe_id: int = 0
    thread_id: int = 0
    cycle: int = 0
    extensions: dict[str, JsonValue] = field(default_factory=dict)

    def __post_init__(self) -> None:
        domains = (
            (self.scalar, ScalarState),
            (self.block, BlockState),
            (self.tile, TileState),
            (self.shared, SharedState),
            (self.memory, MemoryState),
            (self.fault, FaultState),
        )
        if any(not isinstance(value, expected) for value, expected in domains):
            raise ValueError("architecture state contains an invalid domain")
        for label, value in (
            ("pe_id", self.pe_id),
            ("thread_id", self.thread_id),
            ("cycle", self.cycle),
        ):
            if not isinstance(value, int) or isinstance(value, bool) or value < 0:
                raise ValueError(f"architecture state {label} must be non-negative integer")
        _require_json_object(self.extensions, "architecture state extensions")
        canonical_json(self.as_dict())

    def as_dict(self) -> dict[str, JsonValue]:
        return _json_value(self)

    def canonical_json(self) -> str:
        return canonical_json(self.as_dict())

    def sha256(self) -> str:
        return canonical_hash(self.as_dict())

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "ArchitectureState":
        if not isinstance(value, Mapping):
            raise TypeError("architecture state must be an object")

        def object_value(
            item: Any, label: str, required: tuple[str, ...] = ()
        ) -> dict[str, Any]:
            return dict(_require_exact_object(
                item, f"architecture state {label}", required
            ))

        def descriptor(item: Mapping[str, Any]) -> TileDescriptor:
            return TileDescriptor(**object_value(
                item,
                "descriptor",
                ("dtype", "layout", "shape", "valid_shape", "strides", "extensions"),
            ))

        def tile_value(item: Mapping[str, Any]) -> TileValue:
            row = object_value(
                item,
                "tile value",
                ("descriptor", "data", "defined", "generation", "extensions"),
            )
            row["descriptor"] = descriptor(row["descriptor"])
            return TileValue(**row)

        def shared_tile(item: Mapping[str, Any]) -> SharedTile:
            row = object_value(
                item,
                "shared tile",
                (
                    "descriptor", "data", "defined", "generation",
                    "allocation_mask", "extensions",
                ),
            )
            row["descriptor"] = descriptor(row["descriptor"])
            return SharedTile(**row)

        required_state = (
            "scalar", "block", "tile", "shared", "memory", "fault",
            "pe_id", "thread_id", "cycle", "extensions",
        )
        state_row = object_value(value, "state", required_state)
        scalar = ScalarState(**object_value(
            state_row["scalar"],
            "scalar",
            ("registers", "pc", "tpc", "flags", "mode", "extensions"),
        ))
        block = BlockState(**object_value(
            state_row["block"],
            "block",
            (
                "active", "block_id", "start_pc", "instruction_count",
                "attributes", "extensions",
            ),
        ))
        tile_row = object_value(
            state_row["tile"], "tile", ("registers", "extensions")
        )
        if not isinstance(tile_row.get("registers", {}), Mapping):
            raise ValueError("architecture state tile registers must be an object")
        tile_row["registers"] = {
            key: tile_value(item) for key, item in tile_row["registers"].items()
        }
        tile = TileState(**tile_row)
        shared_row = object_value(
            state_row["shared"],
            "shared",
            ("tiles", "generations", "extensions"),
        )
        if not isinstance(shared_row.get("tiles", {}), Mapping):
            raise ValueError("architecture state shared tiles must be an object")
        shared_row["tiles"] = {
            key: shared_tile(item) for key, item in shared_row["tiles"].items()
        }
        shared = SharedState(**shared_row)
        memory_row = object_value(
            state_row["memory"], "memory", ("cells", "regions", "extensions")
        )
        raw_regions = memory_row["regions"]
        if not isinstance(raw_regions, list):
            raise ValueError("architecture state memory regions must be an array")
        memory_row["regions"] = [
            MemoryRegion(**object_value(
                item,
                "memory region",
                ("base", "size", "permissions", "name", "extensions"),
            ))
            for item in raw_regions
        ]
        memory = MemoryState(**memory_row)
        fault = FaultState(**object_value(
            state_row["fault"],
            "fault",
            (
                "pending", "kind", "code", "address", "instruction",
                "message", "recoverable", "extensions",
            ),
        ))
        return cls(scalar=scalar, block=block, tile=tile, shared=shared,
                   memory=memory, fault=fault, pe_id=state_row["pe_id"],
                   thread_id=state_row["thread_id"], cycle=state_row["cycle"],
                   extensions=_require_object(
                       state_row["extensions"], "architecture state extensions"
                   ))


@dataclass
class StateEnvelope:
    """Versioned envelope for an initial state or standalone snapshot."""

    kind: str
    state: ArchitectureState
    artifact: dict[str, JsonValue] = field(default_factory=dict)
    metadata: dict[str, JsonValue] = field(default_factory=dict)
    schema: str = SCHEMA_ID

    def __post_init__(self) -> None:
        if self.schema != SCHEMA_ID:
            raise ValueError(f"unsupported state envelope schema: {self.schema!r}")
        if self.kind not in {"initial_state", "state_snapshot"}:
            raise ValueError(f"unsupported state envelope kind: {self.kind!r}")
        if not isinstance(self.state, ArchitectureState):
            raise TypeError("state envelope state must be ArchitectureState")
        _require_json_object(self.artifact, "state envelope artifact")
        _require_json_object(self.metadata, "state envelope metadata")
        canonical_json(self.as_dict())

    def as_dict(self) -> dict[str, JsonValue]:
        return _json_value(self)

    def canonical_json(self) -> str:
        return canonical_json(self.as_dict())

    def sha256(self) -> str:
        return canonical_hash(self.as_dict())

    @classmethod
    def initial(cls, state: ArchitectureState, **kwargs: Any) -> "StateEnvelope":
        return cls(kind="initial_state", state=state, **kwargs)

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "StateEnvelope":
        if not isinstance(value, Mapping):
            raise TypeError("state envelope must be an object")
        required = ("schema", "kind", "state", "artifact", "metadata")
        row = _require_exact_object(value, "state envelope", required)
        if row["schema"] != SCHEMA_ID:
            raise ValueError(f"unsupported state envelope schema: {row['schema']!r}")
        kind = row["kind"]
        if kind not in {"initial_state", "state_snapshot"}:
            raise ValueError(f"unsupported state envelope kind: {kind!r}")
        state = row["state"]
        if not isinstance(state, Mapping):
            raise ValueError("state envelope must contain an object state")
        artifact = _require_object(row["artifact"], "state envelope artifact")
        metadata = _require_object(row["metadata"], "state envelope metadata")
        return cls(
            schema=SCHEMA_ID,
            kind=kind,
            state=ArchitectureState.from_dict(state),
            artifact=dict(artifact),
            metadata=dict(metadata),
        )


def state_diff(before: ArchitectureState, after: ArchitectureState) -> dict[str, JsonValue]:
    """Return a compact, deterministic top-level diff for backend diagnostics."""

    left = before.as_dict()
    right = after.as_dict()
    return {
        key: {"before": left[key], "after": right[key]}
        for key in sorted(set(left) | set(right))
        if left.get(key) != right.get(key)
    }


__all__ = [
    "SCHEMA_ID", "SCHEMA_VERSION", "ArchitectureState", "BlockState",
    "FaultState", "MemoryRegion",
    "MemoryState", "ScalarState", "SharedState", "SharedTile", "StateEnvelope",
    "TileDescriptor", "TileState", "TileValue", "canonical_hash", "canonical_json",
    "state_diff",
]
