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

There is no supported compatibility decoder, explicit-width step command,
arbitrary instruction-handler call, or alternate runtime commit path. Runtime
image, memory, stack, reset, restore, and snapshot-lifecycle classes are not
part of the package. Architectural-state DTOs are passive serialization data;
they cannot initialize or mutate the live runner.

Any future accelerated backend must preserve this boundary and prove parity
before admission. It may not introduce a private PTO decoder, opcode table,
termination recognizer, or semantic fallback.
