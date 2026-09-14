# ELF smoke runs

`asl-model-elf-smoke` is the short bring-up entry point for running a compiled
ELF through the ASL-backed runtime. It performs bounded execution and reports
whether the requested prefix committed; it does not claim numerical
correctness without a result contract and an independent golden.

The wrapper discovers the generated ASL from `PTO_SPEC_ROOT` (or the sibling
`pto-spec` checkout) and uses the ASLRef checkout selected by the normal worker
configuration. A typical four-PE run is:

```bash
scripts/asl-model-elf-smoke \
  --pto-spec ../pto-spec \
  --pe-count 4 \
  --worker-scope per-pe \
  --parallel-pe-steps \
  --max-instructions 500 \
  path/to/program.elf
```

The default stack policy is `after-image`: the runtime places non-overlapping
stack banks after the highest PT_LOAD address. Use `--stack-policy explicit`
with `--stack-top` when an ABI requires a fixed address. `--stack-policy
disabled` is useful for diagnosing a program that must not touch the stack.

The ELF machine check defaults to the current PTO value `0xe9`. Pass another
explicit `--expected-machine` value only when diagnosing an older compiler
bring-up image; the observed machine remains included in the JSON result.

Use `--manifest-out run.json` to save the complete step list, machine value,
resolved stack layout, ASLRef identity, and termination reason.
The document is explicitly marked with schema `pto-asl-model-smoke-v1`,
`validation_level=smoke`, and `closure_eligible=false`. It also records the
note, model-lock, sidecar, and golden status so missing release evidence is
never silently accepted.

Generate a current-ISA scalar carrier from the locked PTO-SPEC catalog, then
run it:

```bash
python3 tools/generate_smoke_elf.py \
  --pto-spec ../pto-spec build/scalar-add-smoke.elf
scripts/asl-model-elf-smoke \
  --pto-spec ../pto-spec \
  --expected-machine 0xe9 \
  build/scalar-add-smoke.elf
```

`max-instructions` means exactly that: the run stops after that many steps. A
run that stops at the bound has not finished, so it is reported as
`status=unfinished` and is not a pass, even when every requested step
committed. Only an observed terminal event is reported as `status=passed`;
ASL faults, host-memory failures and backend errors are reported as failed.
The manifest keeps the distinction explicit: `termination` names why the run
stopped and `complete` is true only for a terminal event.

`--timeout-s` bounds how long the host waits for one worker protocol line, so a
step that computes for longer than the budget without emitting one is reported
as `termination=step_timeout` with a `step_timeout` step status. That is a
host budget failure, not an ASL rejection; a step the model actually refused
keeps the `step_failed`/`rejected` shape and its fault code.

`core` worker scope is an explicit diagnostic experiment because current PTO
ASL does not expose complete per-PE context state. It requires
`--experimental-core` and must not be used as conformance or promotion
evidence.

`--parallel-pe-steps` starts independent per-PE workers concurrently and runs
each scheduling round through isolated memory overlays. Reads and writes are
committed in deterministic PE order only when the parallel result is equivalent
to that order. A read-after-write conflict automatically discards the workers
and reruns the ELF serially from its initial state.

The generator reads one accepted unconstrained scalar form and its match bits
from `spec/catalog/scalar-forms.json`. It does not patch a failing production
ELF or embed a second copy of PTO instruction encodings in this repository.
