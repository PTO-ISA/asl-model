"""Generic multi-PE ELF execution orchestration for the ASL model.

This module owns scheduling and lifecycle only.  It does not decode or
implement an instruction.  The default executor sends every fetched encoding
to one persistent ASLRef worker per PE. A single-worker mode is exposed for
capability probing, but is rejected for multiple contexts until the ASL state
model provides PE-scoped TPC, queues, block state, and faults.
"""

from __future__ import annotations

import concurrent.futures
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Protocol

from ..embedded import EmbeddedAslWorker, EmbeddedWorkerTimeout
from .completion import AslCompletionPolicy
from .elf import ElfLoader
from .host_memory import HostMemoryBridge
from .config import RuntimeLayout
from .profile import AslModelProfile
from .parallel_memory import ParallelMemoryConflict, ParallelMemoryCoordinator
from .protocol import ElfLoadRequest, InstructionRequest, ProgramImage, ProgramSegment


def _runtime_layout_dict(layout: RuntimeLayout) -> dict[str, object]:
    """Return stable, JSON-friendly layout metadata for run reports."""

    return {
        "stack_policy": layout.policy,
        "pe_count": layout.pe_count,
        "image_range": {"start": layout.image_start, "end": layout.image_end},
        "stack_banks": [
            {
                "pe_id": stack.pe_id,
                "base": stack.base,
                "end": stack.end,
                "size": stack.size,
                "top": stack.top,
                "stack_pointer": stack.stack_pointer,
                "red_zone": stack.red_zone,
            }
            for stack in layout.stacks
        ],
    }


@dataclass
class PeContext:
    """Mutable execution context owned by one logical PE/thread."""

    pe_id: int
    thread_id: int
    pc: int
    active: bool = True
    finished: bool = False
    instruction_count: int = 0
    metadata: dict[str, object] = field(default_factory=dict)


def host_failure_status(error: BaseException) -> str:
    """Name a host-side execution failure without implying an ASL decision.

    A worker budget timeout means ASL never decided anything about the
    instruction, so it must not be reported as an ASL step failure.
    """

    if isinstance(error, EmbeddedWorkerTimeout):
        return "step_timeout"
    return "runtime_error"


@dataclass(frozen=True)
class InstructionExecution:
    """Backend-neutral outcome of one instruction transition."""

    status: str
    returncode: int
    next_pc: int | None = None
    finished: bool = False
    error: str | None = None
    fault_code: int | None = None

    @property
    def ok(self) -> bool:
        return self.returncode == 0 and self.status in {
            "committed",
            "passed",
            "executed",
        }


class InstructionExecutor(Protocol):
    """Executes one already-fetched instruction for a PE context."""

    def start(self, image: ProgramImage, contexts: tuple[PeContext, ...]) -> None: ...

    def decode_length(self, context: PeContext, encoding: int) -> int: ...

    def execute(
        self, context: PeContext, request: InstructionRequest
    ) -> InstructionExecution: ...

    def close(self) -> None: ...


class UnsupportedPeStateScope(RuntimeError):
    """The ASL state model cannot represent the requested PE contexts."""


class ControlFlowPolicy(Protocol):
    """Select the next PC after a successful semantic transition."""

    def next_pc(
        self,
        context: PeContext,
        request: InstructionRequest,
        execution: InstructionExecution,
    ) -> int: ...


class SequentialControlFlow:
    """Fallback policy for a backend that does not expose PC writeback yet."""

    def next_pc(self, context, request, execution) -> int:
        if execution.next_pc is not None:
            return execution.next_pc
        return request.pc + len(request.encoding)


class FinisherPolicy(Protocol):
    """Decide whether a context has reached an architectural finish point."""

    def observe(
        self,
        context: PeContext,
        request: InstructionRequest,
        execution: InstructionExecution,
    ) -> bool: ...


class ExecutionFinisher:
    """Use an explicit backend-provided finish bit; never infer from ELF names."""

    def observe(self, context, request, execution) -> bool:
        return execution.finished


class CallbackFinisher:
    """Adapt a runtime-specific finisher predicate to the common contract."""

    def __init__(
        self,
        callback: Callable[[PeContext, InstructionRequest, InstructionExecution], bool],
    ):
        self.callback = callback

    def observe(self, context, request, execution) -> bool:
        return bool(self.callback(context, request, execution))


@dataclass(frozen=True)
class MultiPeStep:
    index: int
    pe_id: int
    thread_id: int
    address: int
    instruction: int
    length_bits: int
    status: str
    returncode: int
    next_pc: int | None = None
    finished: bool = False
    error: str | None = None
    fault_code: int | None = None
    elapsed_ms: float = 0.0

    def as_dict(self) -> dict[str, object]:
        return {
            "index": self.index,
            "pe_id": self.pe_id,
            "thread_id": self.thread_id,
            "address": self.address,
            "instruction": self.instruction,
            "length_bits": self.length_bits,
            "status": self.status,
            "returncode": self.returncode,
            "next_pc": self.next_pc,
            "finished": self.finished,
            "error": self.error,
            "fault_code": self.fault_code,
            "elapsed_ms": self.elapsed_ms,
        }


@dataclass(frozen=True)
class MultiPeRunResult:
    image: ProgramImage
    contexts: tuple[PeContext, ...]
    steps: tuple[MultiPeStep, ...]
    artifact: Mapping[str, str] = field(default_factory=dict)
    termination: str = "unknown"
    model_profile: str = "portable"
    runtime_layout: RuntimeLayout | None = None
    runtime_metrics: Mapping[str, object] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        if not self.steps or any(step.returncode != 0 for step in self.steps):
            return False
        # A run that stopped at the instruction bound did not finish, so it is
        # not a pass even when every step committed.
        return self.termination == "all_finished" and all(
            context.finished for context in self.contexts
        )

    def as_dict(self) -> dict[str, object]:
        return {
            "schema": "pto-asl-model-smoke-v1",
            "validation_level": "smoke",
            "closure_eligible": False,
            "pto_isa_note_status": self.image.metadata.get(
                "pto_isa_note_status", "not-validated"
            ),
            "model_lock_status": "not-provided",
            "sidecar_status": "not-provided",
            "golden_status": "not-provided",
            "run_kind": "multi-pe",
            "status": (
                "passed"
                if self.ok
                else (
                    "unfinished"
                    if self.termination == "max_instructions"
                    and all(step.returncode == 0 for step in self.steps)
                    else "failed"
                )
            ),
            "termination": self.termination,
            "model_profile": self.model_profile,
            "entry_point": self.image.entry_point,
            "machine": self.image.metadata.get("machine"),
            "segment_count": self.image.segment_count,
            "pe_count": len(self.contexts),
            "runtime_layout": (
                _runtime_layout_dict(self.runtime_layout)
                if self.runtime_layout is not None
                else None
            ),
            "contexts": [
                {
                    "pe_id": context.pe_id,
                    "thread_id": context.thread_id,
                    "pc": context.pc,
                    "active": context.active,
                    "finished": context.finished,
                    "instruction_count": context.instruction_count,
                }
                for context in self.contexts
            ],
            "steps": [step.as_dict() for step in self.steps],
            "artifact": dict(self.artifact),
            "runtime_metrics": dict(self.runtime_metrics),
        }


class AslWorkerExecutor:
    """Instruction executor backed by persistent ASL worker process(es).

    ``per-pe`` is the compatibility mode. ``single`` uses one ASL VM and
    selects a PE before each instruction, but is intentionally restricted to
    one logical PE: the current ASL state has PE-scoped GPRs while TPC,
    temporary queues, block state, and fault state remain shared.
    """

    def __init__(
        self,
        pto_spec_root: Path,
        *,
        timeout_s: float = 120.0,
        completion_policy: AslCompletionPolicy | None = None,
        initial_source: str | Callable[[PeContext], str] = "",
        worker_factory: Callable[..., EmbeddedAslWorker] = EmbeddedAslWorker,
        memory_bridge: HostMemoryBridge | None = None,
        stack_pointer: int | None = None,
        stack_size: int = 0,
        stack_policy: str = "after-image",
        stack_gap: int = 0x1000,
        stack_stride: int | None = None,
        red_zone: int = 16,
        worker_scope: str = "per-pe",
        experimental_core: bool = False,
        parallel_pe_steps: bool = False,
        model_profile: str = "portable",
        cache_root: Path | None = None,
    ):
        self.pto_spec_root = Path(pto_spec_root).resolve()
        self.timeout_s = timeout_s
        if completion_policy is not None:
            self.completion_policy = completion_policy
        else:
            try:
                self.completion_policy = AslCompletionPolicy(self.pto_spec_root)
            except FileNotFoundError:
                # Unit tests may inject a fake image without a PTO checkout;
                # real ELF runs still construct the policy from the ASL tree.
                self.completion_policy = AslCompletionPolicy(
                    self.pto_spec_root, enabled=False
                )
        self.initial_source = initial_source
        self.worker_factory = worker_factory
        if worker_scope not in {"per-pe", "single", "core"}:
            raise ValueError("worker_scope must be 'per-pe', 'core', or 'single'")
        if worker_scope == "core" and not experimental_core:
            raise UnsupportedPeStateScope(
                "core scope is experimental because PTO ASL does not yet expose "
                "complete per-PE context state; pass experimental_core=True explicitly"
            )
        self.worker_scope = worker_scope
        self.experimental_core = experimental_core
        if parallel_pe_steps and worker_scope != "per-pe":
            raise UnsupportedPeStateScope(
                "parallel PE steps require worker_scope='per-pe'"
            )
        self.parallel_pe_steps = parallel_pe_steps
        self.memory_bridge = memory_bridge or HostMemoryBridge(
            unmapped_policy="zero" if model_profile == "linx-runtime" else "deny"
        )
        self.stack_pointer = stack_pointer
        self.stack_size = stack_size
        self.stack_policy = stack_policy
        self.stack_gap = stack_gap
        self.stack_stride = stack_stride
        self.red_zone = red_zone
        self.runtime_layout: RuntimeLayout | None = None
        self.model_profile = AslModelProfile.select(model_profile)
        self.cache_root = cache_root
        self.profile_spec: Path | None = None
        self.workers: dict[int, EmbeddedAslWorker] = {}
        self._worker_memory_generation: dict[int, int] = {}
        self.artifact: dict[str, str] = {}
        self.metrics: dict[str, object] = {}
        self._parallel_memory: ParallelMemoryCoordinator | None = None
        self._parallel_views = {}

    def start(self, image, contexts) -> None:
        # ``single`` is retained as the historical one-context probe. Core is
        # an explicit experiment until PTO ASL exposes complete per-PE context
        # state. Check this before constructing a worker so invalid
        # configurations fail deterministically.
        if self.worker_scope == "single" and len(contexts) != 1:
            raise UnsupportedPeStateScope(
                "single-worker scope is a one-context probe; use "
                "worker_scope='per-pe' for multiple PE contexts"
            )
        try:
            self.profile_spec = self.model_profile.materialize(
                self.pto_spec_root, self.cache_root
            )
        except FileNotFoundError:
            # Test doubles may intentionally use a synthetic root without a
            # generated artifact. Real workers still fail closed in
            # ``_start_worker`` when no profile spec is available.
            self.profile_spec = None
        self.runtime_layout = RuntimeLayout.resolve(
            image,
            policy=self.stack_policy,
            stack_top=self.stack_pointer,
            stack_size=self.stack_size,
            stack_gap=self.stack_gap,
            stack_stride=self.stack_stride,
            red_zone=self.red_zone,
            pe_count=len(contexts),
        )
        self.memory_bridge.load_image(image, runtime_layout=self.runtime_layout)
        self.close()
        if self.worker_scope in {"single", "core"}:
            worker = self._make_worker(None)
            self._start_worker(worker, self._shared_worker_source(contexts))
            worker.ping()
            select_pe = getattr(worker, "select_pe", None)
            if select_pe is not None:
                select_pe(0)
            self.workers = {context.pe_id: worker for context in contexts}
            self._worker_memory_generation = {
                context.pe_id: self.memory_bridge.generation for context in contexts
            }
            if worker.identity is not None:
                self.artifact = worker.identity.as_dict()
            return
        workers_by_pe = {
            context.pe_id: self._make_worker(context.pe_id) for context in contexts
        }
        created_workers = list(workers_by_pe.values())
        if self.profile_spec is not None and contexts:
            first_worker = workers_by_pe[contexts[0].pe_id]
            ensure_built = getattr(first_worker, "_ensure_built", None)
            if callable(ensure_built):
                first_worker.identity, _ = ensure_built(self.profile_spec)

        def start_context(context: PeContext):
            source = (
                self.initial_source(context)
                if callable(self.initial_source)
                else self.initial_source
            )
            worker = workers_by_pe[context.pe_id]
            worker_source = (
                "SelectMemoryEventAgent("
                f"{context.pe_id} as MemoryAgentId);\n"
                f"WriteTPC(Zeros{{PTO_XLEN}} + 0x{context.pc:x});"
            )
            if self.runtime_layout.stacks:
                worker_source += (
                    f" WriteGPR({self.model_profile.frame_sp_index}, "
                    f"Zeros{{PTO_XLEN}} + 0x{self.memory_bridge.stack_pointer_for(context.pe_id):x});"
                )
            worker_source += f"\n{source}"
            worker_source = worker_source.strip()
            self._start_worker(worker, worker_source)
            worker.ping()
            select_pe = getattr(worker, "select_pe", None)
            if select_pe is not None:
                select_pe(context.pe_id)
            return context.pe_id, worker

        try:
            parallel_start = (
                len(contexts) > 1
                and not callable(self.initial_source)
                and not self.initial_source.strip()
            )
            if parallel_start:
                with concurrent.futures.ThreadPoolExecutor(
                    max_workers=len(contexts), thread_name_prefix="asl-worker-start"
                ) as pool:
                    started = list(pool.map(start_context, contexts))
            else:
                started = [start_context(context) for context in contexts]
        except Exception:
            for worker in created_workers:
                worker.stop()
            raise
        self.workers = dict(started)
        self._worker_memory_generation = {
            context.pe_id: self.memory_bridge.generation for context in contexts
        }
        for context in contexts:
            identity = self.workers[context.pe_id].identity
            if identity is not None:
                self.artifact = identity.as_dict()
                break

    def _make_worker(self, pe_id: int | None):
        def read_byte(address: int) -> int:
            if self._parallel_memory is not None and pe_id is not None:
                return self._parallel_views[pe_id].read_byte(address)
            return self.memory_bridge.read_byte(address)

        def read_chunk(address: int, size: int) -> bytes:
            if self._parallel_memory is not None and pe_id is not None:
                return self._parallel_views[pe_id].read_chunk(address, size)
            return self.memory_bridge.read_chunk(address, size)

        def write_byte(address: int, value: int) -> None:
            if self._parallel_memory is not None and pe_id is not None:
                self._parallel_views[pe_id].write_byte(address, value)
                return
            self.memory_bridge.write_byte(address, value)

        return self.worker_factory(
            self.pto_spec_root,
            timeout_s=self.timeout_s,
            cache_root=self.cache_root,
            memory_read=read_byte,
            memory_read_chunk=read_chunk,
            memory_write=write_byte,
        )

    def begin_parallel_round(self, contexts: list[PeContext]) -> None:
        if not self.parallel_pe_steps:
            return
        if self._parallel_memory is not None:
            raise RuntimeError("parallel memory round is already active")
        self._parallel_memory = ParallelMemoryCoordinator(self.memory_bridge)
        self._parallel_views = {
            context.pe_id: self._parallel_memory.view(context.pe_id)
            for context in contexts
        }

    def commit_parallel_round(self) -> None:
        if self._parallel_memory is None:
            return
        try:
            self._parallel_memory.commit()
        finally:
            self._parallel_memory = None
            self._parallel_views = {}

    def rollback_parallel_round(self) -> None:
        if self._parallel_memory is None:
            return
        try:
            if not self._parallel_memory.closed:
                self._parallel_memory.rollback()
        finally:
            self._parallel_memory = None
            self._parallel_views = {}

    def _start_worker(self, worker, source: str) -> None:
        if self.profile_spec is None:
            worker.start(source)
            return
        worker.start(source, spec_path=self.profile_spec)

    def _shared_worker_source(self, contexts: tuple[PeContext, ...]) -> str:
        parts = []
        if self.worker_scope == "core":
            # Give every PE its own stored execution context: start from the
            # post-reset context, inject that PE's entry state, then capture it
            # back.  The scalar register file is already per-PE, so only the
            # execution context has to be seeded per PE.
            for context in contexts:
                parts.extend(
                    (
                        f"SelectMemoryEventAgent({context.pe_id} as MemoryAgentId);",
                        f"InstallPEContext({context.pe_id} as MemoryAgentId);",
                        f"WriteTPC(Zeros{{PTO_XLEN}} + 0x{context.pc:x});",
                    )
                )
                if self.runtime_layout and self.runtime_layout.stacks:
                    parts.append(
                        f"WriteGPR({self.model_profile.frame_sp_index}, "
                        f"Zeros{{PTO_XLEN}} + 0x{self.memory_bridge.stack_pointer_for(context.pe_id):x});"
                    )
                if callable(self.initial_source):
                    source = self.initial_source(context).strip()
                    if source:
                        parts.append(source)
                parts.append(
                    f"CapturePEContext({context.pe_id} as MemoryAgentId);"
                )
            parts.extend(
                (
                    "SelectMemoryEventAgent(0 as MemoryAgentId);",
                    "InstallPEContext(0 as MemoryAgentId);",
                )
            )
            return "\n".join(parts)
        parts = [
            "SelectMemoryEventAgent(0 as MemoryAgentId);",
            f"WriteTPC(Zeros{{PTO_XLEN}} + 0x{contexts[0].pc:x});",
        ]
        if self.runtime_layout and self.runtime_layout.stacks:
            for context in contexts:
                parts.extend(
                    (
                        f"SelectMemoryEventAgent({context.pe_id} as MemoryAgentId);",
                        f"WriteGPR({self.model_profile.frame_sp_index}, "
                        f"Zeros{{PTO_XLEN}} + 0x{self.memory_bridge.stack_pointer_for(context.pe_id):x});",
                    )
                )
        if callable(self.initial_source):
            for context in contexts:
                source = self.initial_source(context).strip()
                if source:
                    parts.extend(
                        (
                            f"SelectMemoryEventAgent({context.pe_id} as MemoryAgentId);",
                            source,
                        )
                    )
        elif self.initial_source.strip():
            parts.append(self.initial_source.strip())
        parts.extend(
            (
                "SelectMemoryEventAgent(0 as MemoryAgentId);",
                f"WriteTPC(Zeros{{PTO_XLEN}} + 0x{contexts[0].pc:x});",
            )
        )
        return "\n".join(parts)

    def begin_round(self) -> None:
        """Mark a deterministic scheduler round without semantic inference."""

    def decode_length(self, context, encoding) -> int:
        worker = self.workers[context.pe_id]
        length = worker.decode_length(encoding)
        if length not in {16, 32, 48, 64}:
            return 0
        return length

    def execute(self, context, request) -> InstructionExecution:
        try:
            worker = self.workers[context.pe_id]
            self._synchronize_worker_memory(context.pe_id, worker)
            if self.worker_scope == "core":
                install = getattr(worker, "install_pe_context", None)
                capture = getattr(worker, "capture_pe_context", None)
                if install is None or capture is None:
                    raise UnsupportedPeStateScope(
                        "core worker scope requires install_pe_context and "
                        "capture_pe_context on its worker"
                    )
                worker.select_pe(context.pe_id)
                install(context.pe_id)
            elif self.worker_scope == "single":
                select_pe = getattr(worker, "select_pe", None)
                set_tpc = getattr(worker, "set_tpc", None)
                if select_pe is None or set_tpc is None:
                    raise UnsupportedPeStateScope(
                        "single-worker executor requires select_pe and set_tpc on its worker"
                    )
                select_pe(context.pe_id)
                set_tpc(request.pc)
            status = worker.step(
                int.from_bytes(request.encoding, "little"), len(request.encoding) * 8
            )
            if self.worker_scope == "core":
                # Store this PE's context back before another PE can install
                # its own, so no PE observes another PE's live state.
                worker.capture_pe_context(context.pe_id)
            next_pc = worker.peek_tpc() if status == 0 else None
            terminal_pending = (
                worker.peek_terminal_pending()
                if status == 0 and hasattr(worker, "peek_terminal_pending")
                else False
            )
            fault_code = worker.peek_fault() if status != 0 else None
            self._worker_memory_generation[context.pe_id] = (
                self.memory_bridge.generation
            )
        except Exception as error:  # worker errors are part of the report
            return InstructionExecution(
                host_failure_status(error), 2, error=str(error)
            )
        execution = InstructionExecution(
            status="committed" if status == 0 else "rejected",
            returncode=0 if status == 0 else 1,
            next_pc=next_pc,
            finished=terminal_pending,
            fault_code=fault_code,
        )
        return execution

    def step_next(
        self, context: PeContext
    ) -> tuple[InstructionRequest, InstructionExecution]:
        """Execute one ASL-owned fetch, width selection, and transition."""

        worker = self.workers[context.pe_id]
        try:
            self._synchronize_worker_memory(context.pe_id, worker)
            if self.worker_scope == "core":
                install = getattr(worker, "install_pe_context", None)
                capture = getattr(worker, "capture_pe_context", None)
                if install is None or capture is None:
                    raise UnsupportedPeStateScope(
                        "core worker scope requires install_pe_context and "
                        "capture_pe_context on its worker"
                    )
                worker.select_pe(context.pe_id)
                install(context.pe_id)
            elif self.worker_scope == "single":
                worker.select_pe(context.pe_id)
                worker.set_tpc(context.pc)
            step = worker.step_auto()
            if self.worker_scope == "core":
                worker.capture_pe_context(context.pe_id)
            if step.length_bits not in {16, 32, 48, 64}:
                raise RuntimeError(
                    f"ASL returned invalid instruction width {step.length_bits}"
                )
            request = InstructionRequest(
                pc=context.pc,
                encoding=step.instruction.to_bytes(step.length_bits // 8, "little"),
                pe_id=context.pe_id,
                thread_id=context.thread_id,
            )
            finished = (
                worker.peek_terminal_pending()
                if step.status == 0 and hasattr(worker, "peek_terminal_pending")
                else False
            )
            self._worker_memory_generation[context.pe_id] = (
                self.memory_bridge.generation
            )
            return request, InstructionExecution(
                status="committed" if step.status == 0 else "rejected",
                returncode=0 if step.status == 0 else 1,
                next_pc=step.tpc,
                finished=finished,
                fault_code=step.fault_code if step.status != 0 else None,
            )

        except Exception as error:
            return (
                InstructionRequest(
                    pc=context.pc,
                    # The request type requires a representable instruction
                    # width. This synthetic zero halfword is never executed;
                    # it only carries context into the failed-step report.
                    encoding=b"\0\0",
                    pe_id=context.pe_id,
                    thread_id=context.thread_id,
                ),
                InstructionExecution(
                    host_failure_status(error), 2, error=str(error)
                ),
            )

    def _synchronize_worker_memory(self, pe_id: int, worker) -> None:
        generation = self.memory_bridge.generation
        if self._worker_memory_generation.get(pe_id) != generation:
            worker.clear_memory_cache()
            self._worker_memory_generation[pe_id] = generation

    def close(self) -> None:
        self.rollback_parallel_round()
        protocol = {
            "chunk_reads": 0,
            "chunk_read_bytes": 0,
            "byte_reads": 0,
            "byte_writes": 0,
        }
        stopped: set[int] = set()
        for worker in self.workers.values():
            if id(worker) in stopped:
                continue
            stats = getattr(worker, "memory_protocol_stats", None)
            if callable(stats):
                for name, value in stats().items():
                    protocol[name] = protocol.get(name, 0) + value
            worker.stop()
            stopped.add(id(worker))
        self.workers.clear()
        self._worker_memory_generation.clear()
        self.metrics.clear()
        self.metrics["memory_protocol"] = protocol


class AslMultiPeElfRunner:
    """Round-robin multi-PE ELF runner with replaceable runtime policies."""

    def __init__(
        self,
        pto_spec_root: Path,
        *,
        timeout_s: float = 120.0,
        completion_policy: AslCompletionPolicy | None = None,
        model_base: int | None = None,
        memory_bridge: HostMemoryBridge | None = None,
        stack_pointer: int | None = None,
        stack_size: int = 0,
        stack_policy: str = "after-image",
        stack_gap: int = 0x1000,
        stack_stride: int | None = None,
        red_zone: int = 16,
        worker_scope: str = "per-pe",
        experimental_core: bool = False,
        parallel_pe_steps: bool = False,
        model_profile: str = "portable",
        cache_root: Path | None = None,
        expected_machine: int | None = None,
    ):
        self.pto_spec_root = Path(pto_spec_root).resolve()
        self.timeout_s = timeout_s
        if completion_policy is not None:
            self.completion_policy = completion_policy
        else:
            try:
                self.completion_policy = AslCompletionPolicy(self.pto_spec_root)
            except FileNotFoundError:
                self.completion_policy = AslCompletionPolicy(
                    self.pto_spec_root, enabled=False
                )
        self.model_base = model_base
        self.memory_bridge = memory_bridge
        self.stack_pointer = stack_pointer
        self.stack_size = stack_size
        self.stack_policy = stack_policy
        self.stack_gap = stack_gap
        self.stack_stride = stack_stride
        self.red_zone = red_zone
        if worker_scope not in {"per-pe", "single", "core"}:
            raise ValueError("worker_scope must be 'per-pe', 'core', or 'single'")
        if worker_scope == "core" and not experimental_core:
            raise UnsupportedPeStateScope(
                "core scope is experimental because PTO ASL does not yet expose "
                "complete per-PE context state; pass experimental_core=True explicitly"
            )
        self.worker_scope = worker_scope
        self.experimental_core = experimental_core
        if parallel_pe_steps and worker_scope != "per-pe":
            raise UnsupportedPeStateScope(
                "parallel PE steps require worker_scope='per-pe'"
            )
        self.parallel_pe_steps = parallel_pe_steps
        self.model_profile = AslModelProfile.select(model_profile)
        self.cache_root = cache_root
        self.expected_machine = expected_machine
        if self.memory_bridge is None:
            self.memory_bridge = HostMemoryBridge(
                unmapped_policy="zero" if model_profile == "linx-runtime" else "deny"
            )

    def run(
        self,
        elf_path: Path,
        *,
        pe_count: int = 1,
        max_instructions: int = 1,
        length_bits: int | None = None,
        executor_factory: Callable[[], InstructionExecutor] | None = None,
        control_flow: ControlFlowPolicy | None = None,
        finisher: FinisherPolicy | None = None,
        initial_source: str | Callable[[PeContext], str] = "",
    ) -> MultiPeRunResult:
        image = ElfLoader().load(
            ElfLoadRequest(
                path=Path(elf_path),
                requested_base=self.model_base,
                expected_machine=self.expected_machine,
                metadata={"model_profile": self.model_profile.name},
            )
        )
        return self.run_image(
            image,
            pe_count=pe_count,
            max_instructions=max_instructions,
            length_bits=length_bits,
            executor_factory=executor_factory,
            control_flow=control_flow,
            finisher=finisher,
            initial_source=initial_source,
        )

    def run_image(
        self,
        image: ProgramImage,
        *,
        pe_count: int = 1,
        max_instructions: int = 1,
        length_bits: int | None = None,
        executor_factory: Callable[[], InstructionExecutor] | None = None,
        control_flow: ControlFlowPolicy | None = None,
        finisher: FinisherPolicy | None = None,
        initial_source: str | Callable[[PeContext], str] = "",
    ) -> MultiPeRunResult:
        if pe_count <= 0:
            raise ValueError("pe_count must be positive")
        if max_instructions <= 0:
            raise ValueError("max_instructions must be positive")
        if length_bits is not None and length_bits not in {16, 32, 48, 64}:
            raise ValueError("length_bits must be one of 16, 32, 48, or 64")
        host_fetch_enabled = any(
            name == "PTO_MODEL_HOST_MEMORY" and value == "TRUE"
            for name, value in self.model_profile.switches
        )
        if executor_factory is None and length_bits is None and not host_fetch_enabled:
            raise UnsupportedPeStateScope(
                "automatic multi-PE fetch requires model_profile='linx-runtime'; "
                "portable diagnostics must provide an explicit length_bits"
            )
        if self._segment_for_pc(image, image.entry_point) is None:
            raise ValueError("ELF entry point is not inside a PT_LOAD segment")
        contexts = tuple(
            PeContext(index, index, image.entry_point) for index in range(pe_count)
        )
        runtime_layout = RuntimeLayout.resolve(
            image,
            policy=self.stack_policy,
            stack_top=self.stack_pointer,
            stack_size=self.stack_size,
            stack_gap=self.stack_gap,
            stack_stride=self.stack_stride,
            red_zone=self.red_zone,
            pe_count=pe_count,
        )
        executor = (
            executor_factory()
            if executor_factory is not None
            else AslWorkerExecutor(
                self.pto_spec_root,
                timeout_s=self.timeout_s,
                initial_source=initial_source,
                memory_bridge=self.memory_bridge,
                stack_pointer=self.stack_pointer,
                stack_size=self.stack_size,
                stack_policy=self.stack_policy,
                stack_gap=self.stack_gap,
                stack_stride=self.stack_stride,
                red_zone=self.red_zone,
                worker_scope=self.worker_scope,
                experimental_core=self.experimental_core,
                parallel_pe_steps=self.parallel_pe_steps,
                model_profile=self.model_profile.name,
                cache_root=self.cache_root,
            )
        )
        if self.parallel_pe_steps and pe_count > 1:
            required = (
                "step_next",
                "begin_parallel_round",
                "commit_parallel_round",
                "rollback_parallel_round",
            )
            missing = [
                name for name in required if not callable(getattr(executor, name, None))
            ]
            if missing:
                raise UnsupportedPeStateScope(
                    "parallel PE executor is missing capability: " + ", ".join(missing)
                )
        flow = control_flow or SequentialControlFlow()
        finish = finisher or ExecutionFinisher()
        completion = self.completion_policy
        steps: list[MultiPeStep] = []
        termination = "max_instructions"
        parallel_fallback = False
        runtime_metrics: dict[str, object] = {}
        parallel_metrics: dict[str, object] = {
            "attempted": False,
            "rounds": 0,
            "commits": 0,
            "fallback": False,
        }
        parallel_attempt_started: float | None = None

        def execute_context(context: PeContext):
            step_started = time.perf_counter()
            step_next = getattr(executor, "step_next", None)
            if length_bits is None and callable(step_next):
                request, execution = step_next(context)
                width = len(request.encoding) * 8
                return (
                    request,
                    execution,
                    width,
                    (time.perf_counter() - step_started) * 1000.0,
                )
            segment = self._segment_for_pc(image, context.pc)
            if segment is None:
                return "PC is not inside an executable PT_LOAD segment"
            offset = context.pc - segment.address
            if offset + 2 > len(segment.data):
                return "instruction fetch is truncated"
            encoded = int.from_bytes(
                segment.data[offset : offset + 8].ljust(8, b"\0"), "little"
            )
            width = length_bits or executor.decode_length(context, encoded)
            if width not in {16, 32, 48, 64}:
                return "instruction width could not be determined"
            if offset + width // 8 > len(segment.data):
                return "instruction fetch is truncated"
            request = InstructionRequest(
                pc=context.pc,
                encoding=segment.data[offset : offset + width // 8],
                pe_id=context.pe_id,
                thread_id=context.thread_id,
            )
            execution = executor.execute(context, request)
            return (
                request,
                execution,
                width,
                (time.perf_counter() - step_started) * 1000.0,
            )

        def record_outcome(context: PeContext, outcome) -> str | None:
            request, execution, width, elapsed_ms = outcome
            terminal = execution.ok and completion.is_terminal(
                int.from_bytes(request.encoding, "little"), width
            )
            if terminal and not execution.finished:
                execution = InstructionExecution(
                    status=execution.status,
                    returncode=execution.returncode,
                    next_pc=execution.next_pc,
                    finished=True,
                    error=execution.error,
                    fault_code=execution.fault_code,
                )
            next_pc = (
                flow.next_pc(context, request, execution) if execution.ok else None
            )
            if execution.ok:
                context.pc = next_pc if next_pc is not None else context.pc
                context.instruction_count += 1
            finished = execution.ok and finish.observe(context, request, execution)
            if finished:
                context.active = False
                context.finished = True
            elif not execution.ok:
                context.active = False
            steps.append(
                MultiPeStep(
                    index=len(steps),
                    pe_id=context.pe_id,
                    thread_id=context.thread_id,
                    address=request.pc,
                    instruction=int.from_bytes(request.encoding, "little"),
                    length_bits=width,
                    status=execution.status,
                    returncode=execution.returncode,
                    next_pc=next_pc,
                    finished=finished,
                    error=execution.error,
                    fault_code=execution.fault_code,
                    elapsed_ms=elapsed_ms,
                )
            )
            if execution.ok:
                return None
            if execution.status in {"step_timeout", "runtime_error"}:
                return execution.status
            return "step_failed"

        step_pool = None
        try:
            executor.start(image, contexts)
            if self.parallel_pe_steps and pe_count > 1:
                step_pool = concurrent.futures.ThreadPoolExecutor(
                    max_workers=pe_count, thread_name_prefix="asl-pe-step"
                )
            while len(steps) < max_instructions:
                begin_round = getattr(executor, "begin_round", None)
                if begin_round is not None:
                    begin_round()
                scheduled = self._schedule_contexts(contexts)
                scheduled = scheduled[: max_instructions - len(steps)]
                if not scheduled:
                    termination = "all_finished"
                    break
                can_parallel = bool(
                    step_pool is not None
                    and len(scheduled) > 1
                    and length_bits is None
                    and callable(getattr(executor, "step_next", None))
                )
                if can_parallel:
                    if parallel_attempt_started is None:
                        parallel_attempt_started = time.perf_counter()
                    parallel_metrics["attempted"] = True
                    parallel_metrics["rounds"] = int(parallel_metrics["rounds"]) + 1
                    executor.begin_parallel_round(scheduled)
                    outcomes = list(step_pool.map(execute_context, scheduled))
                    failure_index = next(
                        (
                            index
                            for index, outcome in enumerate(outcomes)
                            if isinstance(outcome, str) or not outcome[1].ok
                        ),
                        None,
                    )
                    if failure_index is not None:
                        executor.rollback_parallel_round()
                        if failure_index != 0:
                            parallel_metrics.update(
                                {
                                    "fallback": True,
                                    "fallback_reason": "later_pe_step_failed",
                                    "failure_index": failure_index,
                                    "failure_pe_id": scheduled[failure_index].pe_id,
                                }
                            )
                            parallel_fallback = True
                            break
                        outcome = outcomes[0]
                        if isinstance(outcome, str):
                            return self._failed_fetch_result(
                                image,
                                contexts,
                                steps,
                                executor,
                                runtime_layout,
                                scheduled[0],
                                outcome,
                            )
                        termination = (
                            record_outcome(scheduled[0], outcome) or "step_failed"
                        )
                        return MultiPeRunResult(
                            image,
                            contexts,
                            tuple(steps),
                            getattr(executor, "artifact", {}),
                            termination,
                            self.model_profile.name,
                            runtime_layout,
                            runtime_metrics,
                        )
                    try:
                        executor.commit_parallel_round()
                        parallel_metrics["commits"] = (
                            int(parallel_metrics["commits"]) + 1
                        )
                    except ParallelMemoryConflict as error:
                        executor.rollback_parallel_round()
                        parallel_metrics.update(
                            {
                                "fallback": True,
                                "fallback_reason": "memory_conflict",
                                "writer_pe_id": error.writer_pe_id,
                                "reader_pe_id": error.reader_pe_id,
                                "addresses": list(error.addresses),
                            }
                        )
                        parallel_fallback = True
                        break
                    for context, outcome in zip(scheduled, outcomes):
                        record_outcome(context, outcome)
                else:
                    for context in scheduled:
                        outcome = execute_context(context)
                        if isinstance(outcome, str):
                            return self._failed_fetch_result(
                                image,
                                contexts,
                                steps,
                                executor,
                                runtime_layout,
                                context,
                                outcome,
                            )
                        failure = record_outcome(context, outcome)
                        if failure is not None:
                            termination = failure
                            return MultiPeRunResult(
                                image,
                                contexts,
                                tuple(steps),
                                getattr(executor, "artifact", {}),
                                termination,
                                self.model_profile.name,
                                runtime_layout,
                                runtime_metrics,
                            )
                if all(not context.active for context in contexts):
                    termination = "all_finished"
                    break
        finally:
            if step_pool is not None:
                step_pool.shutdown(wait=True)
            executor.close()
            runtime_metrics.update(getattr(executor, "metrics", {}))
            if parallel_attempt_started is not None:
                parallel_metrics["discarded_elapsed_ms"] = (
                    (time.perf_counter() - parallel_attempt_started) * 1000.0
                    if parallel_fallback
                    else 0.0
                )
            runtime_metrics["parallel"] = parallel_metrics
        if parallel_fallback:
            original = self.parallel_pe_steps
            self.parallel_pe_steps = False
            try:
                serial_result = self.run_image(
                    image,
                    pe_count=pe_count,
                    max_instructions=max_instructions,
                    length_bits=length_bits,
                    executor_factory=executor_factory,
                    control_flow=control_flow,
                    finisher=finisher,
                    initial_source=initial_source,
                )
                metrics = dict(serial_result.runtime_metrics)
                metrics["parallel"] = parallel_metrics
                return replace(serial_result, runtime_metrics=metrics)
            finally:
                self.parallel_pe_steps = original
        return MultiPeRunResult(
            image,
            contexts,
            tuple(steps),
            getattr(executor, "artifact", {}),
            termination,
            self.model_profile.name,
            runtime_layout,
            runtime_metrics,
        )

    @staticmethod
    def _schedule_contexts(contexts):
        """Return active contexts without decoding instruction semantics."""

        return [context for context in contexts if context.active]

    @staticmethod
    def _segment_for_pc(image: ProgramImage, pc: int) -> ProgramSegment | None:
        return next(
            (
                segment
                for segment in image.segments
                if "x" in segment.permissions
                and segment.address <= pc < segment.address + len(segment.data)
            ),
            None,
        )

    def _failed_fetch_result(
        self,
        image: ProgramImage,
        contexts: tuple[PeContext, ...],
        steps: list[MultiPeStep],
        executor: InstructionExecutor,
        runtime_layout: RuntimeLayout,
        context: PeContext,
        error: str,
    ) -> MultiPeRunResult:
        context.active = False
        steps.append(
            MultiPeStep(
                index=len(steps),
                pe_id=context.pe_id,
                thread_id=context.thread_id,
                address=context.pc,
                instruction=0,
                length_bits=0,
                status="fetch_failed",
                returncode=1,
                error=error,
            )
        )
        return MultiPeRunResult(
            image,
            contexts,
            tuple(steps),
            getattr(executor, "artifact", {}),
            "step_failed",
            self.model_profile.name,
            runtime_layout,
            getattr(executor, "metrics", {}),
        )


__all__ = [
    "AslMultiPeElfRunner",
    "AslWorkerExecutor",
    "CallbackFinisher",
    "ControlFlowPolicy",
    "ExecutionFinisher",
    "FinisherPolicy",
    "InstructionExecution",
    "InstructionExecutor",
    "MultiPeRunResult",
    "MultiPeStep",
    "PeContext",
    "SequentialControlFlow",
    "UnsupportedPeStateScope",
]
