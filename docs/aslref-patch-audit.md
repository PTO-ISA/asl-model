# ASLRef patch audit

The reference runner consumes ASLRef commit
`5873cbb69312d92b4b97131cff840ec621b12ddf`, exactly matching the PTO-SPEC
`.aslref-version` at the model lock's PTO commit.

The initial implementation applies no patch to ASLRef:

- parser semantic patches: 0;
- typechecker semantic patches: 0;
- interpreter semantic patches: 0;
- standard-library semantic patches: 0.

Consecutive PTO instructions execute through a generated ASL harness in one
ASLRef process. The harness calls only the PTO-owned
`ExecuteNextPTOInstruction` action and uses reference-profile initialization and
observation functions. ELF parsing, memory initialization, stop policy, and
manifest formatting remain outside ASLRef.
