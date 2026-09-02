# PTO ASL Model

Reference functional-model infrastructure for the PTO ISA. The repository
executes PTO-SPEC ASL through the pinned ASLRef implementation and exposes a
consumer boundary for functional runners such as SuperScalarModel `gfrun`.

PTO architectural semantics remain owned by
[`PTO-ISA/pto-spec`](https://github.com/PTO-ISA/pto-spec). This repository owns
model lifecycle, hosted execution, transport, ABI, ELF loading, and validation.

Development changes land through pull requests. The initial ASLRef backend is
tracked by the repository issue list.

## Reference runner

The first closure runs consecutive PTO instructions inside one ASLRef process.
It accepts a checked static ELF whose load segments fit the explicit hosted
memory bound, plus an assembled PTO ASL file:

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
