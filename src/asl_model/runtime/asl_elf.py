"""ELF fetch loop backed by the persistent ASL execution worker."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from ..embedded import EmbeddedAslWorker, EmbeddedWorkerTimeout
from .completion import AslCompletionPolicy
from .elf import ElfLoader
from .host_memory import HostMemoryBridge
from .config import RuntimeLayout
from .profile import AslModelProfile
from .protocol import ElfLoadRequest, ProgramImage


def host_failure_status(error: BaseException) -> str:
    """Name a host-side execution failure without implying an ASL decision.

    A worker budget timeout means ASL never decided anything about the
    instruction, so it must not be reported as an ASL step failure.
    """

    if isinstance(error, EmbeddedWorkerTimeout):
        return "step_timeout"
    return "runtime_error"


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


@dataclass(frozen=True)
class ElfStep:
    index: int
    address: int
    instruction: int
    length_bits: int
    status: str
    returncode: int
    fault_code: int | None = None
    error: str | None = None
    finished: bool = False


@dataclass(frozen=True)
class ElfRunResult:
    image: ProgramImage
    steps: tuple[ElfStep, ...]
    artifact: dict[str, str]
    model_profile: str = "portable"
    termination: str = "unknown"
    runtime_layout: RuntimeLayout | None = None

    @property
    def complete(self) -> bool:
        """Whether ASL published an explicit terminal event."""

        return self.termination == "asl_terminal" and bool(self.steps) and all(
            step.returncode == 0 for step in self.steps
        )

    def as_dict(self) -> dict[str, object]:
        failed = any(step.returncode != 0 for step in self.steps)
        # Only an observed ASL terminal event is a pass.  A run that stopped
        # because it used its whole instruction budget never finished, so it
        # must not be reported as one.
        passed = not failed and self.termination == "asl_terminal"
        if passed:
            status = "passed"
        elif not failed and self.termination == "max_instructions":
            status = "unfinished"
        else:
            status = "failed"
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
            "run_kind": "single-pe",
            # ``unfinished`` is diagnostic progress: every requested step
            # committed, but the run stopped at the instruction bound without
            # reaching a terminal event.  Out-of-image, decode, fault and
            # runtime results remain ``failed``.
            "status": status,
            "complete": self.complete,
            "termination": self.termination,
            "entry_point": self.image.entry_point,
            "machine": self.image.metadata.get("machine"),
            "segment_count": self.image.segment_count,
            "steps": [step.__dict__ for step in self.steps],
            "artifact": self.artifact,
            "model_profile": self.model_profile,
            "runtime_layout": (
                _runtime_layout_dict(self.runtime_layout)
                if self.runtime_layout is not None else None
            ),
        }


class AslElfRunner:
    """Load an ELF, fetch bytes, and execute them through ASL only."""

    def __init__(self, pto_spec_root: Path, *, timeout_s: float = 120.0, completion_policy: AslCompletionPolicy | None = None, model_base: int | None = None, cache_root: Path | None = None):
        self.pto_spec_root = Path(pto_spec_root).resolve()
        self.timeout_s = timeout_s
        self.completion_policy = completion_policy or AslCompletionPolicy(self.pto_spec_root)
        self.model_base = model_base
        self.cache_root = cache_root

    def run(
        self,
        elf_path: Path,
        *,
        length_bits: int | None = None,
        max_instructions: int = 1,
        initial_source: str = "",
        start_symbol: str | None = None,
        model_profile: str = "portable",
        stack_top: int | None = None,
        stack_size: int = 0x01000000,
        stack_policy: str = "after-image",
        stack_gap: int = 0x1000,
        stack_stride: int | None = None,
        red_zone: int = 16,
        expected_machine: int | None = None,
    ) -> ElfRunResult:
        if length_bits is not None and length_bits not in {16, 32, 48, 64}:
            raise ValueError("length_bits must be one of 16, 32, 48, or 64")
        if max_instructions <= 0:
            raise ValueError("max_instructions must be positive")
        image = ElfLoader().load(
            ElfLoadRequest(
                path=Path(elf_path), requested_base=self.model_base,
                expected_machine=expected_machine,
            )
        )
        layout = RuntimeLayout.resolve(
            image, policy=stack_policy, stack_top=stack_top, stack_size=stack_size,
            stack_gap=stack_gap, stack_stride=stack_stride, red_zone=red_zone,
            pe_count=1,
        )
        start_address = image.entry_point if start_symbol is None else image.symbols.get(start_symbol)
        if start_address is None:
            raise ValueError(f"ELF has no symbol: {start_symbol}")
        segment = next(
            (item for item in image.segments if item.address <= start_address < item.address + item.memory_size),
            None,
        )
        if segment is None:
            raise ValueError("ELF entry point is not inside a PT_LOAD segment")
        offset = start_address - segment.address
        initial_offset = offset
        source = initial_source.strip()
        if source:
            source += "\n"
        source += f"WriteTPC(Zeros{{PTO_XLEN}} + 0x{start_address:x});"
        # GPR numbers in the ASL architecture are absolute register indices.
        # The selected ASL profile owns the frame-SP index; keep setup in the
        # host/runtime layer while leaving frame effects to ASL FENTRY/FRET.
        profile = AslModelProfile.select(model_profile)
        sp_index = profile.frame_sp_index
        if layout.stacks:
            source += f" WriteGPR({sp_index}, Zeros{{PTO_XLEN}} + 0x{layout.stack_pointer_for(0):x});"
        steps: list[ElfStep] = []
        termination = "max_instructions"
        profile_spec = profile.materialize(self.pto_spec_root, self.cache_root)
        # Hosted Linx images may carry externally supplied pointers in their
        # ABI frame.  The named profile uses deterministic zero-backed sparse
        # pages for such addresses; portable mode remains fail-closed.
        memory = HostMemoryBridge(
            unmapped_policy="zero" if model_profile == "linx-runtime" else "deny"
        )
        memory.load_image(image, runtime_layout=layout)
        worker = EmbeddedAslWorker(
            self.pto_spec_root,
            timeout_s=self.timeout_s,
            cache_root=self.cache_root,
            memory_read=memory.read_byte,
            memory_read_chunk=memory.read_chunk,
            memory_write=memory.write_byte,
        )
        try:
            worker.start(source, spec_path=profile_spec)
            worker.ping()
            for index in range(max_instructions):
                position = offset
                address = start_address + (position - initial_offset)
                auto_step = getattr(worker, "step_auto", None)
                # The ASL step API fetches through HostReadMemoryByte.  It is
                # safe only for a profile that binds ASL memory to the ELF
                # bridge; the reference portable profile owns a separate
                # bounded byte array and therefore retains the old fetch
                # compatibility path.
                if (
                    length_bits is None
                    and callable(auto_step)
                    and any(
                        name == "PTO_MODEL_HOST_MEMORY" and value == "TRUE"
                        for name, value in profile.switches
                    )
                ):
                    # ASL owns fetch, width selection, and execution.  The
                    # host memory bridge only services byte requests emitted
                    # by the worker; it does not inspect the instruction.
                    try:
                        auto = auto_step()
                    except Exception as error:
                        steps.append(
                            ElfStep(
                                index=index,
                                address=address,
                                instruction=0,
                                length_bits=0,
                                status=host_failure_status(error),
                                returncode=2,
                                error=str(error),
                                finished=False,
                            )
                        )
                        termination = host_failure_status(error)
                        break
                    instruction = auto.instruction
                    current_length = auto.length_bits
                    status = auto.status
                    next_pc = auto.tpc
                    fault_code = auto.fault_code if status != 0 else None
                else:
                    # Explicit-width callers retain the legacy protocol for
                    # hand-authored instruction/session tests.  A worker
                    # adapter predating ``step_auto`` also reaches this
                    # branch, keeping older test doubles and deployments
                    # source-compatible while the ASL API rolls out.
                    if length_bits is None:
                        available = segment.data[position : position + 8]
                        if len(available) < 2:
                            termination = "out_of_image"
                            break
                        encoded = int.from_bytes(available, "little")
                        current_length = worker.decode_length(encoded)
                        if current_length == 0:
                            termination = "decode_failed"
                            break
                    else:
                        current_length = length_bits
                    instruction_bytes = current_length // 8
                    raw = segment.data[position : position + instruction_bytes]
                    if len(raw) != instruction_bytes:
                        termination = "out_of_image"
                        break
                    instruction = int.from_bytes(raw, "little")
                    try:
                        status = worker.step(instruction, current_length)
                    except Exception as error:
                        # Host-service failures (for example an unmapped
                        # guest address) are runtime failures, not Python
                        # exceptions escaping the public run contract.
                        steps.append(
                            ElfStep(
                                index=index,
                                address=address,
                                instruction=instruction,
                                length_bits=current_length,
                                status=host_failure_status(error),
                                returncode=2,
                                error=str(error),
                                finished=False,
                            )
                        )
                        termination = host_failure_status(error)
                        break
                    next_pc = worker.peek_tpc() if status == 0 else None
                    fault_code = worker.peek_fault() if status != 0 else None
                if current_length == 0:
                    termination = "decode_failed"
                    break
                try:
                    terminal_pending = (
                        worker.peek_terminal_pending()
                        if status == 0 and self.completion_policy.is_terminal(
                            instruction, current_length
                        )
                        else False
                    )
                except Exception as error:
                    steps.append(
                        ElfStep(
                            index=index,
                            address=address,
                            instruction=instruction,
                            length_bits=current_length,
                            status=host_failure_status(error),
                            returncode=2,
                            error=str(error),
                            finished=False,
                        )
                    )
                    termination = host_failure_status(error)
                    break
                steps.append(
                    ElfStep(
                        index=index,
                        address=address,
                        instruction=instruction,
                        length_bits=current_length,
                        status="committed" if status == 0 else "rejected",
                        returncode=0 if status == 0 else 1,
                        fault_code=fault_code,
                        finished=terminal_pending,
                    )
                )
                if status != 0:
                    termination = "step_failed"
                    break
                if terminal_pending:
                    termination = "asl_terminal"
                    break
                instruction_bytes = current_length // 8
                if next_pc is None:
                    termination = "step_failed"
                    break
                offset += instruction_bytes
                if next_pc != address + instruction_bytes:
                    offset = next_pc - segment.address
        finally:
            artifact = worker.identity.as_dict() if worker.identity is not None else {}
            worker.stop()
        return ElfRunResult(
            image=image, steps=tuple(steps), artifact=artifact,
            model_profile=model_profile, termination=termination,
            runtime_layout=layout,
        )
