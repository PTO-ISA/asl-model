# Runtime boundary

`asl_model.runtime` owns the neutral boundary between program execution and
instruction semantics. It is independent of the legacy emulator.

The split is:

```text
ElfLoadRequest → HostAdapter.load() → ProgramImage
                                      ↓
                              RuntimeAdapter
                                      ↓
InstructionRequest → SemanticBackend.execute() → ExecutionResult
```

`ProgramImage` and `InstructionRequest` contain no decoder or instruction
implementation. An ELF loader only has to produce `ProgramImage`; a future
ASL loader can therefore be replaced without changing runtime code.

## Contracts

- `GuestMemory` provides checked guest-address reads/writes and explicit
  `rwx` mappings. Unwritten mapped bytes are zero.
- `MemoryTransaction` stages writes and commits or aborts them atomically for
  one instruction.
- `RuntimeAdapter` owns the architectural state, loaded image, fetch/step
  lifecycle, and complete state+memory snapshots.
- `HostAdapter` is the only host-facing dependency. `MockHostAdapter` is
  provided for deterministic tests; it does not parse ELF.
- `SemanticBackend` is the seam for an ASL backend today and a C++ backend
  later. It receives a mutable working state plus a staged memory transaction.

The intended instruction path is:

```python
request = runtime.fetch()
result = runtime.step(request)
```

`RuntimeAdapter.step()` opens a transaction, invokes the semantic backend,
and commits only a successful `ExecutionResult`. A rejected/error result or an
exception aborts staged memory writes and leaves the previous runtime state
unchanged.

## Loading

The runtime deliberately does not embed an ELF parser. A production loader
should accept `ElfLoadRequest` and return a validated `ProgramImage` with
non-overlapping segments, entry point, optional stack pointer, and symbols.
This keeps libelf, PIE policy, interpreter handling, and syscall setup in a
replaceable host adapter.

The mock contract can be exercised with:

```python
image = ProgramImage(
    entry_point=0x1000,
    segments=(ProgramSegment(0x1000, b"...", 0x100, "rx"),),
)
runtime = RuntimeAdapter(MockHostAdapter(image), semantic_backend)
runtime.load(ElfLoadRequest(image=b"test input"))
```

## Sparse host memory

`HostMemoryBridge` maps every `ProgramImage` `PT_LOAD` at its original guest
virtual address.  File-backed bytes are initialized from `segment.data`; the
remaining `memory_size - len(data)` bytes are sparse zero-backed BSS.  An
explicit `(stack_pointer, stack_size)` pair can add a stack mapping.  The
bridge rejects overlaps and permission violations and never selects a base
address implicitly.

The embedded ASL worker exposes a narrow byte protocol.  When ASL invokes
`HostReadMemoryByte` or `HostWriteMemoryByte`, the worker emits `mem_read` or
`mem_write`; Python services the request from `HostMemoryBridge` and returns a
byte/status response.  The same protocol is available through
`EmbeddedAslWorker.read_memory_byte()` and `.write_memory_byte()` for bring-up
tests.  A worker without callbacks fails closed when ASL requests host memory.

## Snapshot boundary

`RuntimeSnapshot` includes the architecture state envelope, sparse guest
memory, loaded program image, and committed instruction count. It is intended
for ASL/native differential setup and replay. Host-only objects (file handles,
threads, syscall queues) are intentionally excluded and remain owned by the
`HostAdapter`.

## ASL-backed ELF smoke path

`ElfLoader` parses ELF64 `PT_LOAD` segments into `ProgramImage`, and
`AslElfRunner` submits instruction steps to the persistent ASL worker.  With
the hosted profile and automatic width selection, ASL owns the fetch, length
selection, fetch faults, and PC transition; the host only services memory
callbacks:

```bash
python3 -m asl_model.cli elf-run \
  --backend asl \
  --elf /path/to/program.elf \
  --model-profile linx-runtime \
  --length-bits auto \
  --max-instructions 1
```

Explicit `--length-bits 16|32|48|64` remains a compatibility path for older
workers and portable bounded-memory images.  The single-context runner is
retained for compatibility, while the multi-PE runner provides the same
fetch/execute contract with per-context scheduling.

## Multi-PE execution

`AslMultiPeElfRunner` adds a deterministic round-robin execution loop with one
`PeContext` per logical PE. `worker_scope="per-pe"` uses one persistent ASL
worker per context for supported independent-context smoke tests.
`worker_scope="core"` uses one persistent ASL worker for earlier cooperative
diagnostics, but current PTO ASL does not expose complete per-PE context state;
callers must pass `experimental_core=True` or `--experimental-core`, and its
output is not promotion evidence. `worker_scope="single"` is reserved for a
one-context capability probe.
Scheduling, control flow, and completion are policies rather than instruction
branches:

```python
runner = AslMultiPeElfRunner(pto_spec_root)
result = runner.run(
    elf_path,
    pe_count=4,
    max_instructions=100,
    finisher=ExecutionFinisher(),
)
```

`ExecutionFinisher` only accepts an explicit finish signal from the semantic
executor. A backend that does not expose one remains bounded by
`max_instructions`; it is never considered finished based on an ELF filename
or a guessed instruction. `CallbackFinisher` and `ControlFlowPolicy` provide
extension points for ABI-specific completion and ASL-owned PC writeback.

The CLI exposes this path with `elf-run --pe-count N`. `--pe-count 1` keeps
the existing single-context runner for compatibility; values greater than one
use the multi-PE loop and default to `per-pe` workers. Automatic ASL-owned
fetch for multi-PE execution requires `--model-profile linx-runtime`;
portable-profile diagnostics must provide an explicit instruction width.

The machine check is opt-in. Use `--expected-machine 0xe9` for the current PTO
ELF profile when a mismatched input should be rejected before ASL starts. During
compiler bring-up the option may be omitted; the observed machine value is
still retained in the run report.

The embedded worker exposes `select_pe <id>`, `peek_pe_gpr <id> <index>`, and
`peek_pe`. These are state-scope probes, not a claim that all architectural
state is PE-local. Once the ASL state owner splits the remaining fields into
PE contexts, the single-worker executor can be extended to save/restore the
full context around each scheduled instruction.

## ELF ABI and model profiles

The ASL architecture uses absolute GPR selectors for ABI state. The frame-SP
register is obtained from the selected ASL profile via
`PTOFrameStackPointerIndex()`; it is not inferred from a host-side mnemonic.
`portable` is the default and preserves the reference profile. The optional
`linx-runtime` profile is selected explicitly with `--model-profile
linx-runtime`; it materializes a profile-specific ASL artifact and records the
profile in the run report.

The multi-PE runner maps one explicit stack bank per PE. Each worker receives
that bank's selected frame-SP value (`stack_top - 16`). No relocation occurs
unless the caller explicitly supplies `model_base`.

## Catalog-derived smoke carrier

`tools/generate_smoke_elf.py` creates a one-instruction ELF carrier from an
accepted unconstrained PTO-SPEC scalar form. The catalog remains the encoding
owner; ASL-MODEL does not embed or repair production instruction words:

```bash
python3 tools/generate_smoke_elf.py \
  --pto-spec ../pto-spec /tmp/scalar-add-smoke.elf
python3 -m asl_model.cli elf-run --backend asl \
  --elf /tmp/scalar-add-smoke.elf \
  --model-profile linx-runtime --pe-count 1 \
  --length-bits auto --max-instructions 1
```
