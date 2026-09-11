"""Backend-neutral runtime and instruction request contracts.

These objects intentionally contain no decoder or instruction semantics.  A
runtime owns program loading, guest memory and architectural state lifetime;
an ASL/native semantic backend consumes :class:`InstructionRequest` inside an
instruction transaction.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping, Protocol

from ..state import ArchitectureState, ExecutionResult, StateEnvelope
from .memory import GuestMemory, MemorySnapshot, MemoryTransaction




@dataclass(frozen=True)
class ProgramSegment:
    """Loadable program segment independent of a particular ELF library."""

    address: int
    data: bytes
    memory_size: int
    permissions: str = "r"
    alignment: int = 1
    name: str = ""

    def __post_init__(self) -> None:
        if self.address < 0 or self.memory_size < len(self.data) or self.memory_size <= 0:
            raise ValueError("invalid program segment address/size")
        if any(flag not in "rwx" for flag in self.permissions):
            raise ValueError("invalid program segment permissions")
        if self.alignment <= 0 or self.alignment & (self.alignment - 1):
            raise ValueError("segment alignment must be a positive power of two")

    def as_dict(self) -> dict[str, object]:
        return {
            "address": self.address,
            "data": self.data.hex(),
            "memory_size": self.memory_size,
            "permissions": self.permissions,
            "alignment": self.alignment,
            "name": self.name,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "ProgramSegment":
        return cls(
            address=int(value["address"]),
            data=bytes.fromhex(str(value.get("data", ""))),
            memory_size=int(value["memory_size"]),
            permissions=str(value.get("permissions", "r")),
            alignment=int(value.get("alignment", 1)),
            name=str(value.get("name", "")),
        )


@dataclass(frozen=True)
class ProgramImage:
    """Neutral loaded-image description; parsing ELF is outside this contract."""

    entry_point: int
    segments: tuple[ProgramSegment, ...]
    stack_pointer: int | None = None
    symbols: Mapping[str, int] = field(default_factory=dict)
    metadata: Mapping[str, Any] = field(default_factory=dict)
    format: str = "elf"

    def __post_init__(self) -> None:
        if self.entry_point < 0 or not self.segments:
            raise ValueError("program image requires an entry point and segments")
        ordered = sorted(self.segments, key=lambda segment: segment.address)
        if any(left.address + left.memory_size > right.address for left, right in zip(ordered, ordered[1:])):
            raise ValueError("program image segments overlap")

    @property
    def segment_count(self) -> int:
        return len(self.segments)

    def as_dict(self) -> dict[str, object]:
        return {
            "entry_point": self.entry_point,
            "segments": [segment.as_dict() for segment in self.segments],
            "stack_pointer": self.stack_pointer,
            "symbols": dict(self.symbols),
            "metadata": dict(self.metadata),
            "format": self.format,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "ProgramImage":
        return cls(
            entry_point=int(value["entry_point"]),
            segments=tuple(ProgramSegment.from_dict(item) for item in value["segments"]),
            stack_pointer=value.get("stack_pointer"),
            symbols=dict(value.get("symbols", {})),
            metadata=dict(value.get("metadata", {})),
            format=str(value.get("format", "elf")),
        )


@dataclass(frozen=True)
class ElfLoadRequest:
    """Input to a host loader without coupling runtime code to libelf."""

    path: Path | None = None
    image: bytes | None = None
    requested_base: int | None = None
    # Optional ABI gate.  It is deliberately opt-in so that hand-authored
    # synthetic ELF images used by loader/unit tests remain format-only tests.
    expected_machine: int | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if (self.path is None) == (self.image is None):
            raise ValueError("ELF load request requires exactly one of path or image")
        if self.path is not None and not isinstance(self.path, Path):
            object.__setattr__(self, "path", Path(self.path))
        if self.requested_base is not None and self.requested_base < 0:
            raise ValueError("requested ELF base cannot be negative")
        if self.expected_machine is not None and not 0 <= self.expected_machine <= 0xFFFF:
            raise ValueError("expected ELF machine must fit the ELF e_machine field")


@dataclass(frozen=True)
class InstructionRequest:
    """One fetched instruction passed from runtime to a semantic backend."""

    pc: int
    encoding: bytes
    mnemonic: str | None = None
    pe_id: int = 0
    thread_id: int = 0
    block_id: int | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.pc < 0 or not self.encoding:
            raise ValueError("instruction request requires pc and non-empty encoding")
        if self.pe_id < 0 or self.thread_id < 0:
            raise ValueError("instruction PE/thread ids cannot be negative")


@dataclass(frozen=True)
class RuntimeSnapshot:
    """Complete restore point for runtime-owned state."""

    state: StateEnvelope
    memory: MemorySnapshot
    image: ProgramImage | None
    instruction_count: int

    def as_dict(self) -> dict[str, object]:
        return {
            "state": self.state.as_dict(),
            "memory": self.memory.as_dict(),
            "image": self.image.as_dict() if self.image is not None else None,
            "instruction_count": self.instruction_count,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "RuntimeSnapshot":
        state = value.get("state")
        memory = value.get("memory")
        if not isinstance(state, Mapping) or not isinstance(memory, Mapping):
            raise ValueError("runtime snapshot requires state and memory objects")
        image = value.get("image")
        return cls(
            state=StateEnvelope.from_dict(state),
            memory=MemorySnapshot.from_dict(memory),
            image=ProgramImage.from_dict(image) if isinstance(image, Mapping) else None,
            instruction_count=int(value.get("instruction_count", 0)),
        )


class HostAdapter(Protocol):
    """Host services exposed to semantics through an explicit narrow boundary."""

    @property
    def memory(self) -> GuestMemory:
        ...

    def load(self, request: ElfLoadRequest) -> ProgramImage:
        ...

    def read_syscall_result(self) -> int | None:
        ...


class SemanticBackend(Protocol):
    """ASL or native implementation of one architectural transition."""

    name: str

    def execute(
        self,
        request: InstructionRequest,
        state: ArchitectureState,
        memory: MemoryTransaction,
    ) -> ExecutionResult:
        ...


__all__ = [
    "ElfLoadRequest", "HostAdapter", "InstructionRequest", "ProgramImage",
    "ProgramSegment", "RuntimeSnapshot", "SemanticBackend",
]
