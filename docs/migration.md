# Standalone checkout guide

This directory is the complete Python-side ASL model boundary.  It can be
installed and tested without importing any other local project.

## Inputs

The model deliberately does not vendor the specification or ASLRef build.
Point it at a PTO specification checkout whose generated artifact is ready:

```bash
export PTO_SPEC_ROOT=/path/to/pto-spec
```

The specification checkout must contain `build/pto-spec.asl`, the generated
decoder/source-order files, `scripts/aslref`, and `.aslref-version`.

## Install and validate

```bash
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -e .
make test
make check PTO_SPEC=/path/to/pto-spec
```

`make test` includes deterministic Python and C/C++ ABI tests. `make check`
adds bytecode compilation and whitespace validation.

## Python data contract

The installable Python package contains strict, passive architectural-state
serialization and path helpers. These DTOs can describe observed memory data,
but they do not map storage, enforce access permissions, initialize a stack,
load an image, or reset/restore a live model. Generated ASL and ASLRef remain
explicit inputs to the canonical `pto_model_run_elf()` runner.

## Extension points

Keep ISA semantics in ASL. The hosted `pto_model_run_elf()` path alone handles
ELF identity, sidecars, live memory, and execution. Reusable Python code is
limited to passive state serialization and repository-path discovery.
