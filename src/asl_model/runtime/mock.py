"""Small deterministic host and semantic adapters for contract tests."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from ..state import ArchitectureState, ExecutionResult
from .memory import GuestMemory, MemoryTransaction
from .protocol import ElfLoadRequest, InstructionRequest, ProgramImage


class MockHostAdapter:
    """Host adapter backed by an in-memory :class:`ProgramImage`.

    It deliberately does not parse ELF bytes.  ELF parsing belongs in a
    replaceable loader that returns ``ProgramImage``; this mock keeps runtime
    tests independent of libelf and of host filesystem state.
    """

    def __init__(self, image: ProgramImage | None = None, *, syscall_result: int | None = None):
        self._memory = GuestMemory()
        self.image = image
        self.syscall_result = syscall_result
        self.events: list[dict[str, Any]] = []

    @property
    def memory(self) -> GuestMemory:
        return self._memory

    def load(self, request: ElfLoadRequest) -> ProgramImage:
        if self.image is None:
            raise RuntimeError("MockHostAdapter requires a prebuilt ProgramImage")
        self.events.append({"event": "load", "path": str(request.path) if request.path else None})
        return self.image

    def read_syscall_result(self) -> int | None:
        return self.syscall_result


class CallbackSemanticBackend:
    """Test backend that exercises runtime transactions without ISA semantics.

    Production ASL/native adapters can use the same ``SemanticBackend``
    protocol.  The callback is responsible for producing an
    ``ExecutionResult``; this class only supplies a stable backend name.
    """

    name = "mock-semantic"

    def __init__(
        self,
        callback: Callable[[InstructionRequest, ArchitectureState, MemoryTransaction], ExecutionResult],
    ) -> None:
        self.callback = callback

    def execute(self, request, state, memory):
        return self.callback(request, state, memory)


__all__ = ["CallbackSemanticBackend", "MockHostAdapter"]
