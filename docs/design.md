# Standalone design

`pto-asl-model` is an integration layer around the executable PTO ASL
specification.  It intentionally keeps the semantic boundary small:

```text
pto_model_run_elf() → canonical runner → ASLRef → ExecuteNextPTOInstruction()
                              ↓
                         result, manifest
```

The package does not import simulator, emulator, benchmark, or compiler
modules.  The specification checkout and its generated artifact are explicit
runtime inputs. This makes the model usable from a clean checkout without
creating a second PTO execution API.

ELF parsing, sidecar validation, artifact identity, and hosted execution stay
behind the single `pto_model_run_elf()` entry. The Python package contains only
passive, strict architectural-state serialization and path helpers alongside
that runner. It has no second image, memory, stack, lifecycle, or snapshot
authority.

The package owns no duplicate instruction handlers or live memory policy. A
future native backend must implement the same observable state contract and be
admitted by differential tests before it is used for bulk workloads.

## Specification lifecycle

The normal lifecycle is:

1. Edit the modular ASL sources in the specification checkout.
2. Regenerate and type-check `build/pto-spec.asl` there.
3. Invoke `pto_model_run_elf()` or `scripts/pto-asl-run` with the exact lock,
   generated ASL artifact, ELF, and sidecar inputs.

Generated ASL artifacts, ASLRef build outputs, and ELF outputs are not package
source files.

## Repository split

This repository owns only the model boundary and its tests.  The following
remain external inputs or downstream consumers:

| Concern | Owner |
| --- | --- |
| ISA semantics, encodings, catalogs | PTO ASL specification |
| ASL interpretation | ASLRef toolchain |
| ELF production | compiler/assembler toolchain |
| high-throughput execution | future native backend |
| timing/performance | timing model |
