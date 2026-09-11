# Standalone checkout guide

This directory is the complete ASL model integration boundary. It can be
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

The installable `pto_asl_model` package contains strict closure, runner, and
passive architectural-state serialization. The `asl_model` package reuses
those exact DTOs and adds non-release smoke/session lifecycle. Generated ASL
and ASLRef remain explicit inputs to both surfaces.

## Extension points

Keep ISA semantics in ASL. The hosted `pto_model_run_elf()` path alone handles
strict ELF identity, lock, sidecar, result, and closure-facing manifests.
`asl-model` may load an ELF and manage worker/storage lifecycle for bounded
bring-up, but it emits only `pto-asl-model-smoke-v1` evidence and may not satisfy
strict closure.
