# Repository layout

This repository owns two deliberately separate consumer surfaces:

- `pto_asl_model` owns the stable strict runner, closure validation, and the
  implementation behind the public `pto_model_run_elf()` C ABI;
- `asl_model` owns non-release developer smoke, persistent sessions, and
  independent per-PE runtime experiments.

PTO-SPEC remains the only owner of instruction semantics. Neither package
contains a decoder or instruction handler.

## Dependency identities

`pto-lock.json` freezes the published identity consumed by strict closure.
`dependencies/pto-spec.lock` separately records the moving development commit
used to validate the non-release runtime. Updating either is an intentional,
reviewed compatibility change; the development lock never grants release
authority or substitutes for strict closure.

Do not commit `.cache/`, generated ASL artifacts, ASLRef build trees, or ELF
outputs. Ordinary CI runs the self-contained unit, ABI, and package checks.
ASL integration jobs must check out the exact development lock and prepare the
pinned ASLRef build before executing the runtime fixture.
