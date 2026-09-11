"""Generic multi-PE ELF execution orchestration for the ASL model.

This module owns scheduling and lifecycle only.  It does not decode or
implement an instruction.  The default executor sends every fetched encoding
to one persistent ASLRef worker per PE. A single-worker mode is exposed for
capability probing, but is rejected for multiple contexts until the ASL state
model provides PE-scoped TPC, queues, block state, and faults.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

from ..embedded import EmbeddedAslWorker
from .completion import AslCompletionPolicy
from .elf import ElfLoader
from .host_memory import HostMemoryBridge
from .config import RuntimeLayout
from .profile import AslModelProfile
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
        return self.returncode == 0 and self.status in {"committed", "passed", "executed"}


class InstructionExecutor(Protocol):
    """Executes one already-fetched instruction for a PE context."""

    def start(self, image: ProgramImage, contexts: tuple[PeContext, ...]) -> None:
        ...

    def decode_length(self, context: PeContext, encoding: int) -> int:
        ...

    def execute(self, context: PeContext, request: InstructionRequest) -> InstructionExecution:
        ...

    def close(self) -> None:
        ...


class UnsupportedPeStateScope(RuntimeError):
    """The ASL state model cannot represent the requested PE contexts."""


class ControlFlowPolicy(Protocol):
    """Select the next PC after a successful semantic transition."""

    def next_pc(
        self,
        context: PeContext,
        request: InstructionRequest,
        execution: InstructionExecution,
    ) -> int:
        ...


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
    ) -> bool:
        ...


class ExecutionFinisher:
    """Use an explicit backend-provided finish bit; never infer from ELF names."""

    def observe(self, context, request, execution) -> bool:
        return execution.finished


class CallbackFinisher:
    """Adapt a runtime-specific finisher predicate to the common contract."""

    def __init__(self, callback: Callable[[PeContext, InstructionRequest, InstructionExecution], bool]):
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

    @property
    def ok(self) -> bool:
        if not self.steps or any(step.returncode != 0 for step in self.steps):
            return False
        if self.termination == "max_instructions":
            return True
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
            "status": "passed" if self.ok else "failed",
            "termination": self.termination,
            "model_profile": self.model_profile,
            "entry_point": self.image.entry_point,
            "machine": self.image.metadata.get("machine"),
            "segment_count": self.image.segment_count,
            "pe_count": len(self.contexts),
            "runtime_layout": (
                _runtime_layout_dict(self.runtime_layout)
                if self.runtime_layout is not None else None
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
                self.completion_policy = AslCompletionPolicy(self.pto_spec_root, enabled=False)
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
        self.artifact: dict[str, str] = {}

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
            self.profile_spec = self.model_profile.materialize(self.pto_spec_root, self.cache_root)
        except FileNotFoundError:
            # Test doubles may intentionally use a synthetic root without a
            # generated artifact. Real workers still fail closed in
            # ``_start_worker`` when no profile spec is available.
            self.profile_spec = None
        self.runtime_layout = RuntimeLayout.resolve(
            image, policy=self.stack_policy, stack_top=self.stack_pointer,
            stack_size=self.stack_size, stack_gap=self.stack_gap,
            stack_stride=self.stack_stride, red_zone=self.red_zone,
            pe_count=len(contexts),
        )
        self.memory_bridge.load_image(image, runtime_layout=self.runtime_layout)
        self.close()
        if self.worker_scope in {"single", "core"}:
            worker = self._make_worker()
            self._start_worker(worker, self._shared_worker_source(contexts))
            worker.ping()
            select_pe = getattr(worker, "select_pe", None)
            if select_pe is not None:
                select_pe(0)
            self.workers = {context.pe_id: worker for context in contexts}
            if worker.identity is not None:
                self.artifact = worker.identity.as_dict()
            return
        for context in contexts:
            source = self.initial_source(context) if callable(self.initial_source) else self.initial_source
            worker = self._make_worker()
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
            self.workers[context.pe_id] = worker
            if worker.identity is not None and not self.artifact:
                self.artifact = worker.identity.as_dict()

    def _make_worker(self):
        return self.worker_factory(
            self.pto_spec_root,
            timeout_s=self.timeout_s,
            cache_root=self.cache_root,
            memory_read=self.memory_bridge.read_byte,
            memory_write=self.memory_bridge.write_byte,
        )

    def _start_worker(self, worker, source: str) -> None:
        if self.profile_spec is None:
            worker.start(source)
            return
        worker.start(source, spec_path=self.profile_spec)

    def _shared_worker_source(self, contexts: tuple[PeContext, ...]) -> str:
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
            if self.worker_scope in {"single", "core"}:
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
            next_pc = worker.peek_tpc() if status == 0 else None
            terminal_pending = (
                worker.peek_terminal_pending()
                if status == 0 and hasattr(worker, "peek_terminal_pending")
                else False
            )
            fault_code = worker.peek_fault() if status != 0 else None
        except Exception as error:  # worker errors are part of the report
            return InstructionExecution("failed", 2, error=str(error))
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
            if self.worker_scope in {"single", "core"}:
                worker.select_pe(context.pe_id)
                worker.set_tpc(context.pc)
            step = worker.step_auto()
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
                InstructionExecution("failed", 2, error=str(error)),
            )

    def close(self) -> None:
        stopped: set[int] = set()
        for worker in self.workers.values():
            if id(worker) in stopped:
                continue
            worker.stop()
            stopped.add(id(worker))
        self.workers.clear()


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
                self.completion_policy = AslCompletionPolicy(self.pto_spec_root, enabled=False)
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
        contexts = tuple(PeContext(index, index, image.entry_point) for index in range(pe_count))
        runtime_layout = RuntimeLayout.resolve(
            image, policy=self.stack_policy, stack_top=self.stack_pointer,
            stack_size=self.stack_size, stack_gap=self.stack_gap,
            stack_stride=self.stack_stride, red_zone=self.red_zone,
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
                model_profile=self.model_profile.name,
                cache_root=self.cache_root,
            )
        )
        flow = control_flow or SequentialControlFlow()
        finish = finisher or ExecutionFinisher()
        completion = self.completion_policy
        steps: list[MultiPeStep] = []
        termination = "max_instructions"
        try:
            executor.start(image, contexts)
            while len(steps) < max_instructions:
                progressed = False
                begin_round = getattr(executor, "begin_round", None)
                if begin_round is not None:
                    begin_round()
                for context in self._schedule_contexts(contexts):
                    if not context.active:
                        continue
                    progressed = True
                    step_next = getattr(executor, "step_next", None)
                    if length_bits is None and callable(step_next):
                        request, execution = step_next(context)
                        width = len(request.encoding) * 8
                    else:
                        segment = self._segment_for_pc(image, context.pc)
                        if segment is None:
                            return self._failed_fetch_result(
                                image, contexts, steps, executor, runtime_layout,
                                context, "PC is not inside an executable PT_LOAD segment",
                            )
                        offset = context.pc - segment.address
                        if offset + 2 > len(segment.data):
                            return self._failed_fetch_result(
                                image, contexts, steps, executor, runtime_layout,
                                context, "instruction fetch is truncated",
                            )
                        encoded = int.from_bytes(
                            segment.data[offset : offset + 8].ljust(8, b"\0"),
                            "little",
                        )
                        width = length_bits or executor.decode_length(context, encoded)
                        if width not in {16, 32, 48, 64}:
                            return self._failed_fetch_result(
                                image, contexts, steps, executor, runtime_layout,
                                context, "instruction width could not be determined",
                            )
                        if offset + width // 8 > len(segment.data):
                            return self._failed_fetch_result(
                                image, contexts, steps, executor, runtime_layout,
                                context, "instruction fetch is truncated",
                            )
                        raw = segment.data[offset : offset + width // 8]
                        request = InstructionRequest(
                            pc=context.pc,
                            encoding=raw,
                            pe_id=context.pe_id,
                            thread_id=context.thread_id,
                        )
                        execution = executor.execute(context, request)
                    terminal = execution.ok and completion.is_terminal(
                        int.from_bytes(request.encoding, "little"), width
                    )
                    # Always execute through the semantic backend first,
                    # including ACRC.  The completion catalog is only a host
                    # observation/ABI hint; it must never replace the ASL
                    # transition.  Generic executors that do not expose a
                    # terminal marker still finish after a successful
                    # catalog-recognized instruction.
                    if terminal and execution.ok and not execution.finished:
                        execution = InstructionExecution(
                            status=execution.status,
                            returncode=execution.returncode,
                            next_pc=execution.next_pc,
                            finished=True,
                            error=execution.error,
                        )
                    next_pc = flow.next_pc(context, request, execution) if execution.ok else None
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
                            index=len(steps), pe_id=context.pe_id, thread_id=context.thread_id,
                            address=request.pc,
                            instruction=int.from_bytes(request.encoding, "little"),
                            length_bits=width, status=execution.status,
                            returncode=execution.returncode, next_pc=next_pc,
                            finished=finished, error=execution.error,
                            fault_code=execution.fault_code,
                        )
                    )
                    if not execution.ok:
                        termination = "step_failed"
                        return MultiPeRunResult(
                            image, contexts, tuple(steps), getattr(executor, "artifact", {}),
                            termination, self.model_profile.name, runtime_layout,
                        )
                    if len(steps) >= max_instructions:
                        break
                if not progressed or all(not context.active for context in contexts):
                    termination = "all_finished"
                    break
        finally:
            executor.close()
        return MultiPeRunResult(
            image, contexts, tuple(steps), getattr(executor, "artifact", {}),
            termination, self.model_profile.name, runtime_layout,
        )

    @staticmethod
    def _schedule_contexts(contexts):
        """Return active contexts without decoding instruction semantics."""

        return [context for context in contexts if context.active]

    @staticmethod
    def _segment_for_pc(image: ProgramImage, pc: int) -> ProgramSegment | None:
        return next(
            (
                segment for segment in image.segments
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
        steps.append(MultiPeStep(
            index=len(steps),
            pe_id=context.pe_id,
            thread_id=context.thread_id,
            address=context.pc,
            instruction=0,
            length_bits=0,
            status="fetch_failed",
            returncode=1,
            error=error,
        ))
        return MultiPeRunResult(
            image,
            contexts,
            tuple(steps),
            getattr(executor, "artifact", {}),
            "step_failed",
            self.model_profile.name,
            runtime_layout,
        )


__all__ = [
    "AslMultiPeElfRunner", "AslWorkerExecutor", "CallbackFinisher",
    "ControlFlowPolicy", "ExecutionFinisher", "FinisherPolicy",
    "InstructionExecution", "InstructionExecutor", "MultiPeRunResult",
    "MultiPeStep", "PeContext", "SequentialControlFlow",
    "UnsupportedPeStateScope",
]
