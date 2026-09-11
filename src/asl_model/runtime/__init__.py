"""Runtime boundary for ASL-owned and future native functional backends."""

from .adapter import (
    InstructionTransaction,
    RuntimeAdapter,
    RuntimeClosedError,
    RuntimeErrorBase,
    TransactionError,
)
from .asl_elf import AslElfRunner, ElfRunResult, ElfStep
from .completion import AslCompletionPolicy, CompletionEncoding
from .elf import ElfFormatError, ElfLoader
from .memory import (
    GuestMemory,
    GuestRegion,
    MemoryAccessError,
    MemorySnapshot,
    MemoryTransaction,
)
from .profile import (
    AslModelProfile,
    LINX_RUNTIME_PROFILE,
    PORTABLE_PROFILE,
    ProfileError,
    materialize_profile,
)
from .host_memory import HostMemoryBridge, MemoryWorker, StackImage
from .config import RuntimeLayout, StackLayout
from .mock import CallbackSemanticBackend, MockHostAdapter
from .multi_elf import (
    AslMultiPeElfRunner,
    AslWorkerExecutor,
    CallbackFinisher,
    ControlFlowPolicy,
    ExecutionFinisher,
    FinisherPolicy,
    InstructionExecution,
    InstructionExecutor,
    MultiPeRunResult,
    MultiPeStep,
    PeContext,
    SequentialControlFlow,
    UnsupportedPeStateScope,
)
from .protocol import (
    ElfLoadRequest,
    HostAdapter,
    InstructionRequest,
    ProgramImage,
    ProgramSegment,
    RuntimeSnapshot,
    SemanticBackend,
)
__all__ = [
    "CallbackSemanticBackend", "ElfLoadRequest", "GuestMemory", "GuestRegion",
    "AslElfRunner", "ElfFormatError", "ElfLoader", "ElfRunResult", "ElfStep",
    "AslCompletionPolicy", "CompletionEncoding",
    "HostAdapter", "InstructionRequest", "InstructionTransaction",
    "MemoryAccessError", "MemorySnapshot", "MemoryTransaction",
    "AslModelProfile", "LINX_RUNTIME_PROFILE", "PORTABLE_PROFILE",
    "ProfileError", "materialize_profile",
    "HostMemoryBridge", "MemoryWorker", "StackImage",
    "RuntimeLayout", "StackLayout",
    "MockHostAdapter", "ProgramImage", "ProgramSegment", "RuntimeAdapter",
    "RuntimeClosedError", "RuntimeErrorBase", "RuntimeSnapshot",
    "SemanticBackend", "TransactionError",
    "AslMultiPeElfRunner", "AslWorkerExecutor", "CallbackFinisher",
    "ControlFlowPolicy", "ExecutionFinisher", "FinisherPolicy",
    "InstructionExecution", "InstructionExecutor", "MultiPeRunResult",
    "MultiPeStep", "PeContext", "SequentialControlFlow", "UnsupportedPeStateScope",
]
