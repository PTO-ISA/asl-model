"""Runtime/semantic-backend adapter with transactional instruction steps."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from ..state import ArchitectureState, ExecutionResult, FaultState, StateEnvelope
from .memory import GuestMemory, MemorySnapshot, MemoryTransaction
from .protocol import (
    ElfLoadRequest,
    HostAdapter,
    InstructionRequest,
    ProgramImage,
    RuntimeSnapshot,
    SemanticBackend,
)


class RuntimeErrorBase(RuntimeError):
    """Base error for runtime lifecycle and transaction failures."""


class RuntimeClosedError(RuntimeErrorBase):
    pass


class TransactionError(RuntimeErrorBase):
    pass


@dataclass
class InstructionTransaction:
    """Working state and staged memory for one instruction transition."""

    runtime: "RuntimeAdapter"
    request: InstructionRequest
    initial_state: ArchitectureState
    state: ArchitectureState
    memory: MemoryTransaction
    _closed: bool = False

    def commit(self, result: ExecutionResult) -> ExecutionResult:
        self._check_open()
        if result.status not in {"committed", "passed", "executed"}:
            raise TransactionError("only a successful execution result can commit")
        result.final_state.pe_id = self.request.pe_id
        result.final_state.thread_id = self.request.thread_id
        self.memory.commit()
        self.runtime._state = ArchitectureState.from_dict(result.final_state.as_dict())
        self.runtime._instruction_count += 1
        self._closed = True
        self.runtime._active = None
        return result

    def abort(self, *, fault: FaultState | None = None) -> None:
        self._check_open()
        self.memory.abort()
        self._closed = True
        self.runtime._active = None

    def _check_open(self) -> None:
        if self._closed:
            raise TransactionError("instruction transaction is closed")


class RuntimeAdapter:
    """Own runtime state while delegating instruction semantics to a backend.

    The adapter deliberately does not decode or implement any instruction. It
    provides loading, fetch, state snapshots, and commit/abort ordering.  A
    future ASL backend and a high-throughput native backend can therefore use
    the same runtime contract.
    """

    def __init__(
        self,
        host: HostAdapter,
        semantic_backend: SemanticBackend | None = None,
        *,
        artifact: Mapping[str, Any] | None = None,
    ) -> None:
        self.host = host
        self.semantic_backend = semantic_backend
        self.artifact = dict(artifact or {})
        self._state = ArchitectureState()
        self._initial_state = ArchitectureState()
        self._initial_memory: MemorySnapshot = self.host.memory.snapshot()
        self._image: ProgramImage | None = None
        self._instruction_count = 0
        self._closed = False
        self._active: InstructionTransaction | None = None

    @property
    def state(self) -> ArchitectureState:
        self._check_open()
        return ArchitectureState.from_dict(self._state.as_dict())

    @property
    def image(self) -> ProgramImage | None:
        self._check_open()
        return self._image

    @property
    def instruction_count(self) -> int:
        return self._instruction_count

    def load(self, request: ElfLoadRequest) -> ProgramImage:
        self._check_open()
        if self._active is not None:
            raise RuntimeErrorBase("cannot load a program during an instruction transaction")
        image = self.host.load(request)
        self.host.memory.restore(GuestMemory().snapshot())
        for segment in image.segments:
            self.host.memory.map_region(
                segment.address,
                segment.memory_size,
                permissions=segment.permissions,
                name=segment.name,
                data=segment.data,
            )
        self._image = image
        self._state.scalar.pc = image.entry_point
        self._state.scalar.tpc = image.entry_point
        if image.stack_pointer is not None:
            self._state.scalar.registers["sp"] = image.stack_pointer
        self._initial_state = ArchitectureState.from_dict(self._state.as_dict())
        self._initial_memory = self.host.memory.snapshot()
        self._instruction_count = 0
        return image

    def reset(self, initial: StateEnvelope | ArchitectureState | None = None) -> None:
        self._check_open()
        if self._active is not None:
            raise RuntimeErrorBase("cannot reset during an instruction transaction")
        if initial is None:
            state = self._initial_state
        elif isinstance(initial, StateEnvelope):
            state = initial.state
        elif isinstance(initial, ArchitectureState):
            state = initial
        else:
            raise TypeError("reset expects ArchitectureState or StateEnvelope")
        self._state = ArchitectureState.from_dict(state.as_dict())
        self.host.memory.restore(self._initial_memory)
        self._instruction_count = 0

    def snapshot(self) -> RuntimeSnapshot:
        self._check_open()
        return RuntimeSnapshot(
            state=StateEnvelope(
                kind="state_snapshot",
                state=self._state,
                artifact=dict(self.artifact),
                metadata={"instruction_count": self._instruction_count},
            ),
            memory=self.host.memory.snapshot(),
            image=self._image,
            instruction_count=self._instruction_count,
        )

    def restore(self, snapshot: RuntimeSnapshot) -> None:
        self._check_open()
        if self._active is not None:
            raise RuntimeErrorBase("cannot restore during an instruction transaction")
        if not isinstance(snapshot, RuntimeSnapshot):
            raise TypeError("runtime restore requires RuntimeSnapshot")
        self._state = ArchitectureState.from_dict(snapshot.state.state.as_dict())
        self.host.memory.restore(snapshot.memory)
        self._image = snapshot.image
        self._instruction_count = snapshot.instruction_count

    def fetch(self, pc: int | None = None, *, size: int = 4) -> InstructionRequest:
        self._check_open()
        address = self._state.scalar.pc if pc is None else pc
        if size <= 0:
            raise ValueError("instruction fetch size must be positive")
        return InstructionRequest(
            pc=address,
            encoding=self.host.memory.read(address, size),
            pe_id=self._state.pe_id,
            thread_id=self._state.thread_id,
        )

    def begin(self, request: InstructionRequest) -> InstructionTransaction:
        self._check_open()
        if self._active is not None:
            raise TransactionError("only one instruction transaction may be active")
        if request.pc != self._state.scalar.pc:
            raise TransactionError(
                f"instruction pc 0x{request.pc:x} does not match runtime pc "
                f"0x{self._state.scalar.pc:x}"
            )
        before = ArchitectureState.from_dict(self._state.as_dict())
        transaction = InstructionTransaction(
            self, request, before, ArchitectureState.from_dict(before.as_dict()), self.host.memory.begin()
        )
        self._active = transaction
        return transaction

    def step(self, request: InstructionRequest) -> ExecutionResult:
        self._check_open()
        if self.semantic_backend is None:
            raise RuntimeErrorBase("runtime has no semantic backend")
        transaction = self.begin(request)
        try:
            result = self.semantic_backend.execute(request, transaction.state, transaction.memory)
            if not isinstance(result, ExecutionResult):
                raise TypeError("semantic backend must return ExecutionResult")
            result.initial_state = StateEnvelope.initial(
                transaction.initial_state, artifact=dict(self.artifact)
            )
            result.artifact = dict(self.artifact)
            if result.status in {"committed", "passed", "executed"}:
                committed = transaction.commit(result)
                self._active = None
                return committed
            transaction.abort()
            self._active = None
            return result
        except Exception:
            if not transaction._closed:
                transaction.abort()
            self._active = None
            raise

    def close(self) -> None:
        if self._closed:
            return
        if self._active is not None and not self._active._closed:
            self._active.abort()
        self._active = None
        self._closed = True

    def __enter__(self) -> "RuntimeAdapter":
        self._check_open()
        return self

    def __exit__(self, exc_type, exc_value, traceback) -> None:
        self.close()

    def _check_open(self) -> None:
        if self._closed:
            raise RuntimeClosedError("runtime adapter is closed")


__all__ = [
    "InstructionTransaction", "RuntimeAdapter", "RuntimeClosedError",
    "RuntimeErrorBase", "TransactionError",
]
