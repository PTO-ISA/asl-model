# PTO ASL model NDF v1

This repository consumes PTO architecture from an exact PTO-SPEC ASL revision.
It does not own instruction semantics.

## Model architecture

- `PTO-MODEL-INSTANCE-001`: one model instance carries one isolated projection
  of PTO architectural state and model lifecycle state.
- `PTO-MODEL-ASLREF-001`: the reference backend executes the pinned ASLRef
  interpreter without parser, typechecker, or interpreter semantic patches.
- `PTO-MODEL-STEP-001`: one step invokes the PTO-owned next-instruction action;
  model observations cannot refine its architectural meaning.

## Hosted runner ABI

- `PTO-MODEL-ELF-001`: the initial runner accepts little-endian static ELF64
  `ET_EXEC` images with bounded, non-overlapping `PT_LOAD` segments and explicit
  stop-PC/result policy. The caller selects a bounded hosted-memory profile;
  its memory and Tile capacities are model bounds, not architectural capacity
  claims.
  The stop policy may select a later occurrence of the same PC, so a return
  label embedded in an entry bundle is not mistaken for program completion.
  A direct-boot profile may start at a verified executable symbol and seed the
  captured return state plus architectural GPR R10 (`ra`) with the verified
  return PC, bypassing platform service-request startup glue. The paired state
  is model ABI: compiler-generated epilogues may select `ra`, while PTO return
  bundle state independently carries the captured target.
- `PTO-MODEL-MANIFEST-001`: each run emits a deterministic JSON manifest bound
  to the exact PTO tree, ASLRef pin, ELF hash, entry, stop policy, result bytes,
  and final TPC.
- `PTO-MODEL-C-ABI-001`: C and C++ consumers invoke the hosted reference runner
  through a versioned standard-layout configuration without observing worker
  transport details.
- `PTO-MODEL-HOST-MEMORY-001`: a model-owned executable MAY bind PTO's
  `ReadPhysicalMemoryByte` and `WritePhysicalMemoryByte` profile hooks to O(1)
  host storage. It MUST NOT replace ASL-owned translation, access permission,
  preflight, ordering, fault, decode, or instruction semantics.
- `PTO-MODEL-HOST-MEMORY-IDENTITY-001`: the host-memory executable MUST link
  the exact pinned ASLRef `asllib` and MUST be content-addressed by the ASLRef
  commit, `asllib` hash, model source hash, and OCaml version. Every run
  manifest MUST record that identity and the selected memory backend.

## Implementation boundary

The implementation assembles a run-specific ASL harness and executes it once
with the pinned ASLRef interpreter. Consecutive instructions execute inside
that single process. Minimal-typecheck runs default to the model-owned
host-memory executable; strict or explicit `reference-array` runs use the
stock pinned `aslref` binary. ELF parsing, memory initialization, stop policy,
backend selection, and manifest generation are model implementation behavior.

The transport may later move to a persistent library backend without changing
PTO architecture or the hosted manifest contract.

## Performance and worker lifecycle

- `PTO-MODEL-FRESH-RESET-001`: the one-shot process backend MAY remove the
  byte-by-byte `_Memory` clear from `ResetProfileState()` only when the pinned
  ASLRef process is newly created, its global storage is initially zero, and
  the process executes exactly one ELF. Every other architectural and model
  state reset MUST remain present.
- `PTO-MODEL-FRESH-RESET-DRIFT-001`: the specialization MUST match the exact
  PTO-owned memory-reset loop once and MUST fail closed if the owner changes.
  A persistent or reused worker MUST NOT select this policy.
- `PTO-MODEL-FRESH-RESET-PARITY-001`: promotion requires the same ELF, PTO
  tree, ASLRef pin, terminal PC, result bytes, and failure class under the full
  and fresh-process reset policies. The run manifest MUST record the selected
  reset policy.
- `PTO-MODEL-WORKER-SNAPSHOT-001`: an accelerated backend may parse and
  initialize the pinned ASL model once, then fork a copy-on-write child for one
  case. The pristine parent MUST NOT execute case commands or return into ASL
  mutable execution after the snapshot point.
- `PTO-MODEL-CASE-ISOLATION-001`: one case child owns all architectural and
  hosted state changes for that run and exits after reporting its terminal
  result. A later case MUST begin from the pristine parent snapshot, not from a
  full in-model reset of the preceding case.
- `PTO-MODEL-WORKER-POOL-001`: pool size is a model resource policy. It MUST be
  bounded independently of GTest concurrency and reported with peak RSS and
  cold-ready, case-run, and recycle timings.
- `PTO-MODEL-DARWIN-STACK-001`: a native Darwin ASLRef worker MUST be linked
  with an explicit main-thread stack large enough for the pinned model. The
  measured minimum working configuration uses a 512 MiB `LC_MAIN` stack; shell
  `ulimit` alone is insufficient.

On the measured host, the original process-per-case path took 526–600 seconds.
Fresh-process reset reduced one 128 KiB TLOAD carrier from 600.6 seconds to
61.0 seconds while preserving its terminal PC and result SHA-256. A persistent
prototype takes about 510 seconds to become ready, then decodes and steps in
less than one millisecond, with roughly 498 MiB peak RSS. Full reusable-state
reset takes 544–582 seconds, so reset-per-case reuse remains rejected; the
snapshot child lifecycle above is the selected direction beyond the one-shot
optimization.

For a compiler-generated scalar throughput ELF, replacing ASL list-backed
physical memory with the host sparse map reduced 2415.72 seconds to 424.93
seconds (5.68x) with identical final TPC and complete 8 KiB result SHA-256.
This optimization is orthogonal to snapshot reuse: it removes linear physical
memory indexing, while snapshot reuse removes repeated parse/startup cost.
