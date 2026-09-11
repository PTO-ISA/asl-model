"""Runtime result types built on the canonical strict state DTOs.

The stable architectural-state schema and validators live in
``pto_asl_model.state``. The developer runtime re-exports those exact types
and adds only its execution-result envelope, so the two public namespaces
cannot drift into incompatible definitions of the same state schema.
"""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass, field
from typing import Any, Mapping

from pto_asl_model.state import (
    SCHEMA_ID,
    SCHEMA_VERSION,
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


JsonValue = Any
EXECUTION_RESULT_SCHEMA_ID = "pto.asl-model.execution-result.v1"


def _require_integer(value: Any, label: str, *, minimum: int | None = None) -> None:
    if not isinstance(value, int) or isinstance(value, bool):
        raise ValueError(f"{label} must be an integer")
    if minimum is not None and value < minimum:
        raise ValueError(f"{label} must be at least {minimum}")


def _require_json_object(value: Any, label: str) -> None:
    if not isinstance(value, dict) or any(not isinstance(key, str) for key in value):
        raise ValueError(f"{label} must be an object with text keys")
    canonical_json(value)


def _exact_object(
    value: Mapping[str, Any], label: str, fields: tuple[str, ...]
) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{label} must be an object")
    missing = [field for field in fields if field not in value]
    unexpected = sorted(set(value) - set(fields))
    if missing:
        raise ValueError(f"{label} is missing required fields: " + ", ".join(missing))
    if unexpected:
        raise ValueError(f"{label} has unexpected fields: " + ", ".join(unexpected))
    return dict(value)


@dataclass
class MemoryEffect:
    """One observed memory effect produced by a runtime backend."""

    kind: str
    address: int
    size: int
    data: list[int] = field(default_factory=list)
    extensions: dict[str, JsonValue] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not isinstance(self.kind, str) or not self.kind:
            raise ValueError("memory effect kind must be non-empty text")
        _require_integer(self.address, "memory effect address", minimum=0)
        _require_integer(self.size, "memory effect size", minimum=0)
        if not isinstance(self.data, list):
            raise ValueError("memory effect data must be an array")
        for index, byte in enumerate(self.data):
            _require_integer(byte, f"memory effect data[{index}]", minimum=0)
            if byte > 255:
                raise ValueError("memory effect data values must be bytes")
        if len(self.data) > self.size:
            raise ValueError("memory effect data exceeds the declared size")
        _require_json_object(self.extensions, "memory effect extensions")

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "MemoryEffect":
        return cls(**_exact_object(
            value,
            "memory effect",
            ("kind", "address", "size", "data", "extensions"),
        ))


@dataclass
class ExecutionResult:
    """Backend-neutral result for one developer-runtime instruction step."""

    instruction: str
    status: str
    initial_state: StateEnvelope
    final_state: ArchitectureState
    memory_effects: list[MemoryEffect] = field(default_factory=list)
    fault: FaultState = field(default_factory=FaultState)
    pc_before: int | None = None
    pc_after: int | None = None
    artifact: dict[str, JsonValue] = field(default_factory=dict)
    metadata: dict[str, JsonValue] = field(default_factory=dict)
    schema: str = EXECUTION_RESULT_SCHEMA_ID
    kind: str = "execution_result"

    def __post_init__(self) -> None:
        if self.schema != EXECUTION_RESULT_SCHEMA_ID:
            raise ValueError(f"unsupported execution result schema: {self.schema!r}")
        if self.kind != "execution_result":
            raise ValueError(f"unsupported execution result kind: {self.kind!r}")
        if not isinstance(self.instruction, str) or not isinstance(self.status, str):
            raise ValueError("execution result instruction and status must be text")
        if not isinstance(self.initial_state, StateEnvelope):
            raise ValueError("execution result initial_state must be StateEnvelope")
        if not isinstance(self.final_state, ArchitectureState):
            raise ValueError("execution result final_state must be ArchitectureState")
        if not isinstance(self.memory_effects, list) or any(
            not isinstance(effect, MemoryEffect) for effect in self.memory_effects
        ):
            raise ValueError("execution result memory_effects must contain MemoryEffect")
        if not isinstance(self.fault, FaultState):
            raise ValueError("execution result fault must be FaultState")
        for label, value in (("pc_before", self.pc_before), ("pc_after", self.pc_after)):
            if value is not None:
                _require_integer(value, f"execution result {label}", minimum=0)
        _require_json_object(self.artifact, "execution result artifact")
        _require_json_object(self.metadata, "execution result metadata")
        canonical_json(self.as_dict())

    def as_dict(self) -> dict[str, JsonValue]:
        return dataclasses.asdict(self)

    def canonical_json(self) -> str:
        return canonical_json(self.as_dict())

    def sha256(self) -> str:
        return canonical_hash(self.as_dict())

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "ExecutionResult":
        row = _exact_object(
            value,
            "execution result",
            (
                "instruction", "status", "initial_state", "final_state",
                "memory_effects", "fault", "pc_before", "pc_after",
                "artifact", "metadata", "schema", "kind",
            ),
        )
        row["initial_state"] = StateEnvelope.from_dict(row["initial_state"])
        row["final_state"] = ArchitectureState.from_dict(row["final_state"])
        effects = row["memory_effects"]
        if not isinstance(effects, list):
            raise ValueError("execution result memory_effects must be an array")
        row["memory_effects"] = [MemoryEffect.from_dict(effect) for effect in effects]
        fault = row["fault"]
        fault_fields = (
            "pending", "kind", "code", "address", "instruction", "message",
            "recoverable", "extensions",
        )
        row["fault"] = FaultState(**_exact_object(
            fault, "execution result fault", fault_fields
        ))
        return cls(**row)


__all__ = [
    "SCHEMA_ID", "SCHEMA_VERSION", "EXECUTION_RESULT_SCHEMA_ID",
    "ArchitectureState", "BlockState", "ExecutionResult", "FaultState",
    "MemoryEffect", "MemoryRegion", "MemoryState", "ScalarState",
    "SharedState", "SharedTile", "StateEnvelope", "TileDescriptor",
    "TileState", "TileValue", "canonical_hash", "canonical_json", "state_diff",
]
