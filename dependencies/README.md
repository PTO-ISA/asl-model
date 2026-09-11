# Runtime dependencies

`pto-spec` is the semantic owner of the ISA. The model consumes its generated
ASL artifact and ASLRef launcher; it does not duplicate instruction handlers.

For a reproducible local setup:

```bash
./scripts/bootstrap.sh
PTO_SPEC_ROOT="$PWD/vendor/pto-spec" make test
```

This development lock records the specification revision used by the
non-release smoke/session runtime. It is distinct from `pto-lock.json`, which
owns the strict published closure identity. Updating this lock is an
intentional compatibility change: run the specification checks, regenerate
its build artifacts, then run this repository's model tests and ELF smoke
tests. It never authorizes a PTO-SPEC tag or release.
