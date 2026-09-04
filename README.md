# PTO ASL Model

Reference functional-model infrastructure for the PTO ISA. The repository
executes PTO-SPEC ASL through the pinned ASLRef implementation and exposes a
consumer boundary for functional runners such as SuperScalarModel `gfrun`.

PTO architectural semantics remain owned by
[`PTO-ISA/pto-spec`](https://github.com/PTO-ISA/pto-spec). This repository owns
hosted execution, ABI, ELF loading, and validation.

Development changes land through pull requests. The initial ASLRef backend is
tracked by the repository issue list.

## Reference runner

The hosted ELF command is the one model entry used by both the command line
tool and the C ABI. It runs consecutive instructions in one ASLRef process;
ASL owns fetch, instruction length selection, decode, legality, faults, and
architectural state transitions. The model owns ELF validation/loading, the
guest-memory image, stop/result handling, and the process boundary.

The Python package also exposes strict, passive architectural-state DTOs and
canonical serialization helpers. They do not map memory, authorize accesses,
load images, initialize stacks, reset or restore execution, or provide another
instruction engine. The hosted runner and PTO ASL remain the sole live memory
and execution authority.

The hosted runner accepts a checked static ELF and an assembled PTO ASL file.

```bash
scripts/pto-asl-run \
  --asl-spec /path/to/pto-spec/build/pto-spec.asl \
  --aslref /path/to/aslref \
  --elf program.elf \
  --stop-pc 0x114 \
  --stop-after-hits 2 \
  --start-pc 0x120 \
  --return-pc 0x114 \
  --max-steps 6 \
  --result-address 0x200 \
  --result-size 4 \
  --memory-bytes 0x20000 \
  --tile-elements 1 \
  --runtime-typecheck minimal \
  --memory-backend host-sparse \
  --result-out result.bin \
  --manifest-out run.json \
  --lock pto-lock.json
```

Run repository checks with `make check`.

## PTO 0.58.5 compiler/model closure

The repository owns the cross-component AVS layer for PTO 0.58.5. The
[`scripts/pto-closure`](scripts/pto-closure) entrypoint validates an exact,
clean PTO-SPEC/LLVM/ASL-MODEL candidate tuple, the PTO-owned NDF and ASLRef
pins, and exact tool binaries before it compiles any case. Every input object
and final ELF must contain exactly one canonical allocatable
`.note.pto.isa` for release `0.58.5`, publication `0.58.5.1`, encoding ABI
`pto-isa-0.58.5-mode-function-v1`, and the release encoding-projection hash.

Closed-loop cases live under [`avs/cases`](avs/cases). They use JSON syntax in
`case.yaml`, a deterministic YAML 1.2 subset requiring no runtime YAML parser.
Each case explicitly maps PTO identities to compiler/model obligations and
contains independently reviewed golden bytes. Repository-owner semantic tests
remain in PTO-SPEC, and LLVM MC/CodeGen/LLD tests remain in LLVM.

The corpus includes the four established PTO functional-model cases migrated
from the 0.58.5 PTO-SPEC baseline: `scalar_stop_pc`, `block_64_stop_pc`,
`tile_tadd_stop_pc`, and `host_exit_group`. Their exact instruction bytes,
link addresses, independent results, PTO requirements, AVS IDs, and expected
instruction-length sequences remain explicit here. C and IR scalar-return
canaries separately exercise compiler-language lanes. Publication 0.58.5.1
adds `cube_reduce_expand_layouts` for direct M16/M32 reduction and expansion
and `cube_internal_acc_hints` for transparent CCTRL cache hints.

The command emits four distinct canonical artifacts:

- `closure-lock.json` freezes repository trees, tools, target, model profile,
  corpus, and selected obligations;
- per-case manifests bind source, commands, object, ELF, note, sidecar,
  golden, result, terminal state, and the hosted runner manifest;
- `closure-semantic-payload.json` contains only reproducible semantic inputs
  and results;
- `closure-run-envelope.json` binds exactly one semantic-payload digest to
  workflow and per-run provenance.

All JSON digests use UTF-8, lexicographically sorted keys, and compact
separators. Files add one trailing newline that is not part of the canonical
object digest. Missing tools, dirty/wrong checkouts, missing impact mappings,
unsupported golden policies, note drift, skips, timeouts, or result mismatches
fail closed. See [`docs/closure.md`](docs/closure.md) for the request shape and
full CLI.

Run focused contract checks with `make closure-check`.

## Performance status

The one-shot process backend uses a fresh-process reset specialization. The
pinned ASLRef initializes fresh global storage to zero, so the runner removes
only the redundant byte-by-byte memory-clear loop while retaining every
register, queue, Tile, bundle, fault, ACR, and system-state reset. The
specialization matches the exact PTO reset loop once and fails closed if that
source shape changes.

Minimal-typecheck runs now default to the model-owned `host-sparse` backend.
It links a small executable against the exact pinned ASLRef `asllib` and binds
only `ReadPhysicalMemoryByte` and `WritePhysicalMemoryByte` to an O(1) sparse
host byte map. Translation, permission, ordering, preflight, faults, decode,
and instruction semantics remain in PTO ASL. The executable is content-addressed
by the ASLRef commit, `asllib` hash, model source hash, and OCaml version.

On the measured Darwin host, `scalar.abs_i32_thr` fell from 2415.72 seconds
with the ASL reference array to 424.93 seconds with host memory, a 5.68x
improvement, while preserving final TPC and the complete 8 KiB result SHA-256.
The earlier fresh-process reset specialization remains active; on a 128 KiB
TLOAD carrier it reduced 600.6 seconds to 61.0 seconds with the same result.
Use `--memory-backend reference-array` for explicit parity checks.

The remaining process path is still parse/startup dominated for short cases.
A native persistent-worker prototype reaches sub-millisecond warm decode/step
latency after initialization, while a full reusable-state reset takes 544–582
seconds.

The next accelerated backend keeps a pristine initialized worker and forks
one copy-on-write child per case. This reuses the parsed/typechecked model while
preserving case isolation. Worker-pool concurrency must be bounded by memory;
the measured initialized worker peaks near 498 MiB RSS. The one-shot
host-memory backend remains the default until that snapshot backend passes its
promotion gates.

The reset contract is in [`docs/model-ndf-v1.md`](docs/model-ndf-v1.md). The
snapshot lifecycle, identity, transport, and promotion gates are in
[`docs/worker-snapshot-design.md`](docs/worker-snapshot-design.md).

Run repository checks with `make check`. The C/C++ consumer links
`PTOASLModel::pto_asl_model` and calls the versioned
`pto_model_run_elf` function declared in `include/pto/pto_asl_model.h`.

The CMake build publishes both `libpto_asl_model.so.2` and the legacy-named
`libpto_asl_model.a` archive. Consumers such as `gfrun` link
`PTOASLModel::pto_asl_model` and use the versioned C ABI. A pure C consumer can
link that shared target. The static `PTOASLModel::pto_asl_model_static` target
contains the C++ implementation and therefore requires a C++ linker; it does
not provide a second ABI. The model library still launches the configured ASL
runner and owns the complete ELF execution. Set the runtime library path to the
installation `lib` directory when invoking a consumer outside an installed
environment.
