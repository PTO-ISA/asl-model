# ASL multi-PE state scope

The new ASL runtime keeps the specification interpreter in one process when
the state can be represented safely. The current ASL declarations do not yet
provide that contract for multiple independently advancing PE contexts.

## What is currently PE-scoped

`asl/arch/programming-model/execution-context.asl` declares `_PEGPRs` as an
array indexed by `MemoryAgentId`. `ReadGPR` and `WriteGPR` resolve through
`_CurrentMemoryAgent`; the worker therefore exposes:

```text
select_pe <id>
peek_pe_gpr <id> <index>
peek_pe
```

These operations are useful for checking the PE register-file mapping and for
future context serialization.

## What is not currently PE-scoped

The same ASL file declares the following as shared globals:

- `_PC` and `_BPC` / TPC-related control state;
- `_TQueue`, `_UQueue`, and their validity bits;
- bundle lifecycle and commit state;
- `_LastFault` and `_FaultAddress`;
- tile, shared-tile, and block state;
- memory/event state and maintenance state.

`_CurrentMemoryAgent` is a selector, not a complete PE context. Selecting PE 1
does not create a private TPC, queue, block, or fault state.

## Runtime decision

`AslWorkerExecutor` keeps the established `worker_scope="per-pe"` behavior for
multi-PE ELF smoke runs. Its `worker_scope="single"` mode uses one persistent
worker only when exactly one PE context is requested. It raises
`UnsupportedPeStateScope` for multiple contexts rather than producing a
plausible-looking but architecturally invalid result.

The next ASL-side contract needed before enabling one-worker multi-PE
execution is an explicit context record (or equivalent state partition) that
covers every state item whose value can differ between independently
scheduled PEs. The runtime can then add `save_pe_context` / `restore_pe_context`
or an ASL-owned context selector and compare complete snapshots before
enabling multiplexing.

An experimental `worker_scope="core"` path is retained only to reproduce and
diagnose earlier smoke runs. Callers must opt in with `experimental_core=True`
or `--experimental-core`. It is not a supported architectural state model,
does not participate in strict closure, and must not decode issuer or command
semantics in Python.
