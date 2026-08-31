# AGENTS.md - PTO ASL Model

## Ownership

- PTO-SPEC ASL/NDF exclusively owns instruction encoding, architectural state,
  legality, faults, ordering, and state transitions.
- This repository owns model lifecycle, transport, ABI, hosted ELF execution,
  memory storage, scheduling, traces, snapshots, and descriptors.
- Never duplicate PTO decode or instruction handlers in this repository.
- The reference backend must use the exact ASLRef commit recorded by PTO-SPEC.
- Changes to parser, typechecker, or interpreter semantics require a separate
  upstream/toolchain review and may not be hidden in a model change.

## Verification

Run the repository test command documented in `README.md` and `git diff --check`
before opening or updating a pull request.
