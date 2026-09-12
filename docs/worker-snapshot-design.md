# Snapshot worker design

This document defines the selected accelerated lifecycle for the PTO ASL
functional model. It is a model implementation contract, not PTO architecture.

## Why snapshot workers

The original process backend was cold-start dominated. On the measured Darwin
host, one specialized run took 526–600 seconds. The fresh-process reset policy
now removes the redundant memory sweep and reduced one 128 KiB TLOAD carrier
from 600.6 seconds to 61.0 seconds without changing its terminal PC or result
SHA-256. A native persistent
prototype takes 510.271 seconds to become ready, after which decode and step
commands take 0.284–0.983 milliseconds. Re-running `ResetProfileState()` takes
544.350–582.447 seconds, so reset-per-case reuse is slower than the current
backend and is rejected.

The one-shot host-memory backend also removes ASL list-backed physical-memory
indexing. On `scalar.abs_i32_thr` it reduced 2415.72 seconds to 424.93 seconds
with identical final TPC and 8 KiB result SHA-256. It remains one process per
case, so the snapshot design below still targets repeated parse/startup cost.

The initialized worker peaks near 498 MiB RSS. Pool concurrency therefore must
be explicit and memory-bounded.

## Selected lifecycle

The worker is an asl-model-owned executable linked against the exact pinned
ASLRef `asllib`; it does not patch the ASLRef parser, typechecker, interpreter,
or standard library.

## Current bounded optimization

Before the cross-run snapshot daemon is promoted, the developer runtime uses
two semantics-preserving optimizations for `per-pe` smoke runs:

- independent ASLRef workers parse and initialize concurrently;
- each scheduling round executes against isolated host-memory overlays, then
  commits writes in deterministic PE order only when no earlier write could
  affect a later read. A conflict discards the speculative workers and reruns
  the complete ELF serially.

Worker memory reads are populated from host-validated chunks and invalidated by
the shared-memory generation after writes. Translation, access checks, faults,
decode, Tile semantics, and instruction execution remain in PTO ASL.

On the measured Darwin host, the frozen `ops-20260911` FP32 four-PE FA prefix
improved as follows while preserving the exact 325-step trace and final fault:

| Measurement | Baseline | Optimized |
| --- | ---: | ---: |
| Four-PE startup and four scalar steps | 46.18 s | 14.78 s |
| FA time to `Fault_TileLegality` at `0x11432` | 393.41 s | 114.38 s |

The optimized trace SHA-256 is
`6887a559d9e33415c1ccc910598a898a13736c6ddc7e1d8907b659ff08a56d5c`.
This benchmark stops at a known architecture/model gap and is not a complete FA
or independent-golden result. The snapshot lifecycle below remains the route
for amortizing initialization across separate ELF runs.

```text
parse and typecheck the specialized PTO specification
  -> initialize the interpreter environment
  -> enter the static worker ASL wrapper
  -> ResetProfileState() exactly once
  -> enter HostReadCommand()
       pristine parent: accept request, fork, wait/reap; never return to ASL
       case child: receive one case, execute one ELF, report, then _exit
```

The fork gate belongs in a small C stub called by the model-owned host
primitive. Keeping the parent inside that stub avoids allocating protocol
objects in the parent OCaml heap and minimizes copy-on-write dirtiness.

## Isolation and identity

Each case child owns all architectural and hosted mutations for exactly one
run. A child is never reset and reused. A failed, timed-out, or crashed child is
discarded; the next case forks from the unchanged parent snapshot.

Workers are keyed by all state that can change behavior:

- PTO tree and ASLRef commit;
- worker executable and protocol version;
- exact `memory_bytes` and `tile_elements` bounds;
- runtime typecheck mode and static wrapper version.

Profiles must not be coalesced into a larger bound because bounds affect
observable access and fault behavior.

## Transport

The shared daemon uses framed Unix-domain `SOCK_STREAM` connections. The
Python runner retains current ELF, sidecar, symbol, hash, and segment
validation, opens the verified ELF once, and passes its read-only descriptor
with `SCM_RIGHTS`. The pristine parent reads only a fixed fork header; the case
child reads variable payload and ELF segments with `pread`.

The request contains start, return, stop, stack, step, result, profile, segment,
and ELF-hash data. The child initializes memory through PTO-owned ASL accessors,
sets both captured return state and R10, then executes only
`ExecuteNextPTOInstruction()` until the stop policy terminates.

## Platform requirements

Darwin workers must be linked with a 512 MiB `LC_MAIN` stack
(`-Wl,-stack_size,0x20000000`). Raising shell stack limits alone is not
sufficient. Linux launchers must set the corresponding stack resource limit.

## Promotion gates

The process backend remains the default until the snapshot backend proves:

1. one cold parent reaches the post-reset fork gate;
2. good–failing–good cases demonstrate pristine isolation;
3. one ELF matches the process backend and independent golden byte-for-byte;
4. eight same-profile children run concurrently without cross-talk;
5. the 124 scalar GTests pass with cold, fork, case, recycle, and memory data;
6. every exact profile required by the 341-case corpus passes;
7. crash, timeout, busy, stale-socket, and parent-restart paths pass;
8. two consecutive complete green runs are reproducible.
