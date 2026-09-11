# Canonical next-instruction contract

PTO has one hosted model entry and one ASL next-instruction entry:

```text
pto_model_run_elf()
  -> pto_asl_model.runner.run()
  -> ExecuteNextPTOInstruction()
```

The hosted runner owns ELF validation, sidecar and lock identity, memory-image
initialization, stop/result policy, manifests, and the ASLRef process. PTO-SPEC
ASL owns fetch, instruction-width selection, decode, legality, faults, PC/TPC,
and architectural state transitions.

There is no compatibility decoder, arbitrary instruction-handler call, or
alternate commit path in the strict runner. The non-release `asl_model`
developer runtime exposes explicit-width session steps and lifecycle classes
for diagnostics, but they do not replace `ExecuteNextPTOInstruction()` in the
strict path and cannot produce closure evidence. Architectural-state DTOs
remain passive serialization data shared by both namespaces.

Any future accelerated backend must preserve this boundary and prove parity
before admission. It may not introduce a private PTO decoder, opcode table,
termination recognizer, or semantic fallback.
