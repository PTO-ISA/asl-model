---
doc_id: ASLMODEL-DOC-CLOSURE
status: active
authority: normative
owner: closure
---

# Compiler-to-model closure requirements

These requirements govern ASL-MODEL validation behavior. They do not restate
or modify PTO instruction semantics.

## Scalar return acceptance {#ASLMODEL-REQ-SCALAR-RETURN-001}
<!-- ndf: kind=requirement modality=must refinement=L1 domain=closure status=active conforms-to=ndf://pto-spec/PTO-C-BSTOP-DECISION-BINDING-001 -->

The closure MUST compile, link, and execute a freestanding scalar program until
the exact hosted return target, then compare the observed result with
independently committed golden bytes.

## C scalar return verification {#ASLMODEL-VERIF-SCALAR-C-001}
<!-- ndf: kind=verification modality=must refinement=L3 domain=compiler status=active verifies=ASLMODEL-REQ-SCALAR-RETURN-001 -->

AVS case `scalar-c-return` MUST satisfy the C compiler and ASL execution
obligation for `ASLMODEL-REQ-SCALAR-RETURN-001`.

## IR scalar return verification {#ASLMODEL-VERIF-SCALAR-IR-001}
<!-- ndf: kind=verification modality=must refinement=L3 domain=compiler status=active verifies=ASLMODEL-REQ-SCALAR-RETURN-001 -->

AVS case `scalar-ir-return` MUST satisfy the LLVM IR lowering and ASL execution
obligation for `ASLMODEL-REQ-SCALAR-RETURN-001`.

## Scalar mixed-length execution {#ASLMODEL-REQ-SCALAR-STOP-PC-001}
<!-- ndf: kind=requirement modality=must refinement=L1 domain=model status=active conforms-to=ndf://pto-spec/PTO-REQ-INSTRUCTION-FETCH-001,ndf://pto-spec/PTO-INST-SCALAR-SW-PCR -->

The closure MUST execute the established 16-, 32-, and 48-bit scalar sequence,
observe its PCR-relative store, and terminate at the declared stop PC.

## Scalar mixed-length verification {#ASLMODEL-VERIF-SCALAR-STOP-PC-001}
<!-- ndf: kind=verification modality=must refinement=L3 domain=model status=active verifies=ASLMODEL-REQ-SCALAR-STOP-PC-001 -->

AVS case `scalar_stop_pc` MUST satisfy the scalar mixed-length execution and
independent-result obligation.

## 64-bit block termination {#ASLMODEL-REQ-BLOCK-64-STOP-PC-001}
<!-- ndf: kind=requirement modality=must refinement=L1 domain=model status=active conforms-to=ndf://pto-spec/PTO-REQ-INSTRUCTION-FETCH-001,ndf://pto-spec/PTO-L-BSTOP-DECISION-BINDING-001 -->

The closure MUST execute the accepted 64-bit block terminator and observe its
declared return TPC.

## 64-bit block verification {#ASLMODEL-VERIF-BLOCK-64-STOP-PC-001}
<!-- ndf: kind=verification modality=must refinement=L3 domain=model status=active verifies=ASLMODEL-REQ-BLOCK-64-STOP-PC-001 -->

AVS case `block_64_stop_pc` MUST satisfy the 64-bit fetch, decode, and terminal
TPC obligation.

## Tile TADD execution {#ASLMODEL-REQ-TILE-TADD-STOP-PC-001}
<!-- ndf: kind=requirement modality=must refinement=L1 domain=tile status=active conforms-to=ndf://pto-spec/PTO-REQ-INSTRUCTION-FETCH-001,ndf://pto-spec/PTO-INST-TILE-TLOAD,ndf://pto-spec/PTO-INST-TILE-TADD,ndf://pto-spec/PTO-INST-TILE-TSTORE -->

The closure MUST execute the established TLOAD, TADD, and TSTORE sequence and
observe the independently specified U32 result.

## Tile TADD verification {#ASLMODEL-VERIF-TILE-TADD-STOP-PC-001}
<!-- ndf: kind=verification modality=must refinement=L3 domain=tile status=active verifies=ASLMODEL-REQ-TILE-TADD-STOP-PC-001 -->

AVS case `tile_tadd_stop_pc` MUST satisfy the Tile load/add/store and terminal
TPC obligation.

## Direct CUBE reduction and expansion layouts {#ASLMODEL-REQ-CUBE-REDUCE-EXPAND-001}
<!-- ndf: kind=requirement modality=must refinement=L1 domain=tile status=active conforms-to=ndf://pto-spec/PTO-TEXPANDS-CONTRACT-001,ndf://pto-spec/PTO-TROWEXPANDADD-CONTRACT-001,ndf://pto-spec/PTO-TROWSUM-CONTRACT-001 -->

The closure MUST compile, link, and execute direct Local CUBE_M16 and CUBE_M32
asymmetric one-row loads, row reductions, nonzero scalar fills, row
expansion-adds, and stores, then compare their distinct results with
independently calculated and committed golden bytes.

## Direct CUBE layout verification {#ASLMODEL-VERIF-CUBE-REDUCE-EXPAND-001}
<!-- ndf: kind=verification modality=must refinement=L3 domain=tile status=active verifies=ASLMODEL-REQ-CUBE-REDUCE-EXPAND-001 -->

AVS case `cube_reduce_expand_layouts` MUST satisfy the direct M16/M32 reduction
and expansion execution obligation.

## Transparent InternalAcc hints {#ASLMODEL-REQ-INTERNAL-ACC-HINTS-001}
<!-- ndf: kind=requirement modality=must refinement=L1 domain=tile status=active conforms-to=ndf://pto-spec/PTO-CUBE-INTERNAL-ACCUMULATOR-001 -->

The closure MUST execute otherwise-identical nonzero explicit-C matrix
accumulation with CCTRL `00` and `11` and MUST observe identical nonzero
architectural results when no final-output post-processing is selected.

## Transparent InternalAcc hint verification {#ASLMODEL-VERIF-INTERNAL-ACC-HINTS-001}
<!-- ndf: kind=verification modality=must refinement=L3 domain=tile status=active verifies=ASLMODEL-REQ-INTERNAL-ACC-HINTS-001 -->

AVS case `cube_internal_acc_hints` MUST satisfy the explicit-C input-prefetch
and raw-output replacement-hint transparency obligation.

## Host exit request {#ASLMODEL-REQ-HOST-EXIT-GROUP-001}
<!-- ndf: kind=requirement modality=must refinement=L1 domain=closure status=active conforms-to=ndf://pto-spec/PTO-REQ-INSTRUCTION-FETCH-001,ndf://pto-spec/PTO-REQ-SCALAR-BODY-ENTRY-001,ndf://pto-spec/PTO-INST-SCALAR-ACRC -->

The closure MUST preserve the established ACRC exit-group request case and
reject publication unless its terminal request contract is observed.

## Host exit verification {#ASLMODEL-VERIF-HOST-EXIT-GROUP-001}
<!-- ndf: kind=verification modality=must refinement=L3 domain=closure status=active verifies=ASLMODEL-REQ-HOST-EXIT-GROUP-001 -->

AVS case `host_exit_group` MUST satisfy the system-block request and terminal
classification obligation.
