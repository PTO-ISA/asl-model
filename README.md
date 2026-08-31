# PTO ASL Model

Reference functional-model infrastructure for the PTO ISA. The repository
executes PTO-SPEC ASL through the pinned ASLRef implementation and exposes a
consumer boundary for functional runners such as SuperScalarModel `gfrun`.

PTO architectural semantics remain owned by
[`PTO-ISA/pto-spec`](https://github.com/PTO-ISA/pto-spec). This repository owns
model lifecycle, hosted execution, transport, ABI, ELF loading, and validation.

Development changes land through pull requests. The initial ASLRef backend is
tracked by the repository issue list.
