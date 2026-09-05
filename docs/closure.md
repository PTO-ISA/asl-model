# PTO-SPEC / LLVM / ASL-MODEL closure

This flow is the ASL-MODEL-owned acceptance boundary for one immutable PTO
release tuple. It does not define instruction meaning. PTO-SPEC ASL/NDF remains
the only ISA authority; the closure consumes that meaning through the assembled
ASL model and exact ASLRef pin.

## Inputs

`scripts/pto-closure` requires an unsigned request containing exact candidate
commits and local checkout paths:

```json
{
  "schema": "pto-closure-request-v1",
  "identity": {
    "release": "0.58.6",
    "publication_version": "0.58.6.0",
    "encoding_abi": "pto-isa-0.58.6-mode-function-v1",
    "encoding_projection_sha256": "a757f2e50ec8050d2131b6b9ad38657511df80cf3f9424d5f009ea6e0cc35839"
  },
  "repositories": {
    "pto_spec": {"repository": "https://github.com/PTO-ISA/pto-spec.git", "commit": "<40 hex>", "path": "/checkout/pto-spec"},
    "llvm": {"repository": "https://github.com/LinxISA/llvm-project.git", "commit": "<40 hex>", "path": "/checkout/llvm-project"},
    "asl_model": {"repository": "https://github.com/PTO-ISA/asl-model.git", "commit": "<40 hex>", "path": "/checkout/asl-model"}
  },
  "policy": {
    "affected_pto_ids": ["PTO-INST-TILE-TADD"],
    "mandatory_case_ids": ["block_64_stop_pc", "cube_internal_acc_hints", "cube_reduce_expand_layouts", "host_exit_group", "scalar-c-return", "scalar-ir-return", "scalar_stop_pc", "tile_tadd_stop_pc"]
  }
}
```

The separately supplied impact document is the unmodified JSON envelope from
`ndf impact pto-release --format json` (`schema_version=0.1`, command
`impact pto-release`, and a successful schema-version-1 report in `data`). The
closure derives affected PTO IDs from the ASL-MODEL conformance targets and
changed instruction identities, then requires exact equality with the request
policy. Every affected ID must have an explicit AVS mapping; there is no
discovery from filenames or disassembly.

The bootstrap release workflow invokes:

```bash
scripts/pto-closure \
  --request closure-request.json \
  --ndf-impact ndf-impact.json \
  --ndf-root /checkout/normative_language \
  --aslref-root /checkout/herdtools7 \
  --asl-spec /checkout/pto-spec/build/pto-spec.asl \
  --clang /checkout/llvm-project/build/bin/clang \
  --llvm-mc /checkout/llvm-project/build/bin/llvm-mc \
  --ld-lld /checkout/llvm-project/build/bin/ld.lld \
  --aslref /checkout/herdtools7/_build/default/asllib/aslref.exe \
  --output /artifacts/pto-closure \
  --workflow-repository PTO-ISA/pto-spec \
  --workflow-path .github/workflows/release.yml \
  --workflow-commit "$(git -C /checkout/pto-spec rev-parse HEAD)" \
  --run-id "$GITHUB_RUN_ID" \
  --run-attempt "$GITHUB_RUN_ATTEMPT" \
  --runner-image "$ImageOS-$ImageVersion" \
  --builder-identity github-hosted
```

The output directory must not already exist. Generated objects, ELFs, linker
scripts, sidecars, results, and receipts stay in that artifact directory and
must not be committed.

## Trust and reproducibility boundary

The CLI verifies checkout origin, full commit, tree, and cleanliness. It also
requires PTO-SPEC to pin normative_language commit
`ed356980ce7ecb2e8482902988d5012fb54058b3` and ASLRef commit
`5873cbb69312d92b4b97131cff840ec621b12ddf`. Tool paths are explicit and their
SHA-256 values enter the final lock. The ASLRef executable must reside under
the checked pinned ASLRef tree.

The stable semantic payload deliberately excludes timestamp, run ID, attempt,
runner image, builder identity, and future attestation data. Those values live
only in the run envelope, so two clean executions of the same tuple can prove
semantic digest equality while retaining distinct provenance.

Bootstrap mode does not authenticate externally supplied receipts. PTO-SPEC's
protected same-run workflow must create and validate these artifacts locally.
