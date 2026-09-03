from __future__ import annotations

import copy
import json
import pathlib
import struct
import tempfile
import unittest
from unittest import mock

from pto_asl_model.closure import (
    CaseStageTimeout,
    _golden_bytes,
    _invocation_path,
    _load_cases,
    _load_impact,
    _model_run_lock,
    _run_model_case,
    _select_cases,
)
from pto_asl_model.closure_artifacts import (
    ASLREF_COMMIT,
    ENCODING_ABI,
    ENCODING_PROJECTION_SHA256,
    NDF_COMMIT,
    PUBLICATION_VERSION,
    RELEASE,
    canonical_json_bytes,
    canonical_sha256,
    validate_lock,
    validate_case,
    validate_run_envelope,
    validate_semantic_payload,
)
from pto_asl_model.elf_note import PTOISANoteError, parse_pto_isa_note
from pto_asl_model.runner import ASLRefTimeoutError


ELF_HEADER = struct.Struct("<16sHHIQQQIHHHHHH")
SECTION_HEADER = struct.Struct("<IIQQQQIIQQ")


def identity() -> dict[str, str]:
    return {
        "release": RELEASE,
        "publication_version": PUBLICATION_VERSION,
        "encoding_abi": ENCODING_ABI,
        "encoding_projection_sha256": ENCODING_PROJECTION_SHA256,
    }


def repository(commit: str) -> dict[str, str]:
    return {
        "repository": "https://github.com/PTO-ISA/example.git",
        "commit": commit,
        "tree": "a" * 40,
    }


def lock() -> dict[str, object]:
    selected = ["obligation.one"]
    repositories = {
        "pto_spec": repository("1" * 40),
        "llvm": repository("2" * 40),
        "asl_model": repository("3" * 40),
        "normative_language": repository(NDF_COMMIT),
        "aslref": repository(ASLREF_COMMIT),
    }
    tools = {
        name: {"path": f"/tools/{name}", "sha256": index * 64}
        for name, index in zip(("clang", "llvm_mc", "ld_lld", "aslref"), "4567")
    }
    return {
        "schema": "pto-closure-lock-v1",
        "identity": identity(),
        "repositories": repositories,
        "tools": tools,
        "target": {"triple": "linx64-unknown-none-elf"},
        "model": {
            "abi": "pto-asl-model-experimental-v2",
            "worker_protocol": "pto-asl-worker-v1",
            "backend": "host-sparse",
            "profile": "bounded-reference-v1",
        },
        "corpus": {"sha256": "8" * 64, "case_ids": ["case.one"]},
        "obligations": {
            "affected_pto_ids": ["PTO-ONE"],
            "selected": selected,
            "sha256": canonical_sha256(selected),
        },
    }


def payload() -> dict[str, object]:
    closure_lock = lock()
    return {
        "schema": "pto-closure-semantic-payload-v1",
        "closure_lock": closure_lock,
        "closure_lock_sha256": canonical_sha256(closure_lock),
        "ndf_impact": {"sha256": "9" * 64, "affected_pto_ids": ["PTO-ONE"]},
        "obligations": {
            "selected": ["obligation.one"], "completed": ["obligation.one"],
            "passed": ["obligation.one"], "failed": [],
        },
        "cases": {
            "selected": ["case.one"], "completed": ["case.one"],
            "passed": ["case.one"], "failed": [], "skipped": [],
            "timeout": [], "unknown": [],
        },
        "case_manifests": [{"case_id": "case.one", "sha256": "b" * 64}],
    }


def make_note_elf(path: pathlib.Path, descriptor: bytes, *, duplicate: bool = False,
                  note_flags: int = 2, note_alignment: int = 4) -> None:
    identification = bytearray(16)
    identification[:7] = b"\x7fELF\x02\x01\x01"
    strings = b"\0.shstrtab\0.note.pto.isa\0"
    name_offset = strings.index(b".note.pto.isa")
    record = struct.pack("<III4s", 4, len(descriptor), 1, b"PTO\0") + descriptor
    record += bytes((-len(record)) % 4)
    strings_offset = ELF_HEADER.size
    note_offset = (strings_offset + len(strings) + 3) & ~3
    second_note_offset = note_offset + len(record)
    section_offset = second_note_offset + (len(record) if duplicate else 0)
    count = 4 if duplicate else 3
    header = ELF_HEADER.pack(
        bytes(identification), 1, 0xE9, 1, 0, 0, section_offset, 0,
        ELF_HEADER.size, 0, 0, SECTION_HEADER.size, count, 1,
    )
    sections = [SECTION_HEADER.pack(0, 0, 0, 0, 0, 0, 0, 0, 0, 0)]
    sections.append(SECTION_HEADER.pack(1, 3, 0, 0, strings_offset, len(strings), 0, 0, 1, 0))
    sections.append(SECTION_HEADER.pack(name_offset, 7, note_flags, 0, note_offset, len(record), 0, 0, note_alignment, 0))
    if duplicate:
        sections.append(SECTION_HEADER.pack(name_offset, 7, 2, 0, second_note_offset, len(record), 0, 0, 4, 0))
    content = header + strings
    content += bytes(note_offset - len(content)) + record
    if duplicate:
        content += record
    content += b"".join(sections)
    path.write_bytes(content)


class ClosureArtifactTests(unittest.TestCase):
    def test_execute_timeout_is_classified_and_cannot_pass_payload(self) -> None:
        with mock.patch(
            "pto_asl_model.runner.run",
            side_effect=ASLRefTimeoutError("hung ASLRef"),
        ):
            with self.assertRaises(CaseStageTimeout) as raised:
                _run_model_case("hung-case", 1.25, mock.sentinel.configuration)
        error = raised.exception
        self.assertEqual(error.case_id, "hung-case")
        self.assertEqual(error.stage, "execute")
        self.assertEqual(error.failure_class, "timeout")
        self.assertIn("case=hung-case stage=execute failure_class=timeout", str(error))

        candidate = payload()
        candidate["cases"]["passed"] = []  # type: ignore[index]
        candidate["cases"]["timeout"] = ["case.one"]  # type: ignore[index]
        with self.assertRaisesRegex(ValueError, "not completely passing"):
            validate_semantic_payload(candidate)

    def test_hex_golden_decodes_to_runner_bytes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = pathlib.Path(directory) / "golden.hex"
            path.write_text("19000000\n", encoding="ascii")
            self.assertEqual(
                _golden_bytes(path, {"golden": {"encoding": "hex"}}),
                b"\x19\x00\x00\x00",
            )

    def test_invocation_path_preserves_driver_symlink_name(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            driver = root / "lld"
            driver.write_text("driver", encoding="utf-8")
            alias = root / "ld.lld"
            alias.symlink_to(driver)
            self.assertEqual(_invocation_path(alias), alias.absolute())
            self.assertEqual(_invocation_path(alias).name, "ld.lld")

    def test_canonical_json_and_digest_are_stable(self) -> None:
        left = {"b": [2, 1], "a": "PTO"}
        right = {"a": "PTO", "b": [2, 1]}
        self.assertEqual(canonical_json_bytes(left), b'{"a":"PTO","b":[2,1]}')
        self.assertEqual(canonical_sha256(left), canonical_sha256(right))

    def test_lock_rejects_extra_fields_and_wrong_ndf_pin(self) -> None:
        candidate = lock()
        validate_lock(candidate)
        candidate["extra"] = True
        with self.assertRaisesRegex(ValueError, "unknown extra"):
            validate_lock(candidate)
        candidate = lock()
        candidate["repositories"]["normative_language"]["commit"] = "f" * 40  # type: ignore[index]
        with self.assertRaisesRegex(ValueError, "normative_language commit"):
            validate_lock(candidate)

    def test_payload_requires_complete_pass_and_envelope_binding(self) -> None:
        candidate = payload()
        validate_semantic_payload(candidate)
        failed = copy.deepcopy(candidate)
        failed["cases"]["passed"] = []  # type: ignore[index]
        with self.assertRaisesRegex(ValueError, "not completely passing"):
            validate_semantic_payload(failed)
        envelope = {
            "schema": "pto-closure-run-envelope-v1",
            "semantic_payload_sha256": canonical_sha256(candidate),
            "workflow": {"repository": "PTO-ISA/pto-spec", "path": ".github/workflows/release.yml", "commit": "c" * 40},
            "run": {"id": "42", "attempt": 1, "timestamp": "2026-09-02T00:00:00Z"},
            "runner": {"image": "sha256:image", "builder_identity": "github-hosted"},
            "artifact_sha256": "d" * 64,
            "attestation": None,
        }
        validate_run_envelope(envelope, candidate)
        second_envelope = copy.deepcopy(envelope)
        second_envelope["run"]["id"] = "43"  # type: ignore[index]
        validate_run_envelope(second_envelope, candidate)
        self.assertEqual(
            envelope["semantic_payload_sha256"],
            second_envelope["semantic_payload_sha256"],
        )
        envelope["semantic_payload_sha256"] = "e" * 64
        with self.assertRaisesRegex(ValueError, "digest mismatch"):
            validate_run_envelope(envelope, candidate)

    def test_all_committed_json_schemas_are_well_formed(self) -> None:
        root = pathlib.Path(__file__).parents[1]
        paths = sorted((root / "schemas").glob("*.schema.json"))
        paths.append(root / "avs" / "schemas" / "case-v1.schema.json")
        self.assertEqual(len(paths), 5)
        for path in paths:
            document = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(document["$schema"], "https://json-schema.org/draft/2020-12/schema")

    def test_pto_note_accepts_one_canonical_0585_record(self) -> None:
        descriptor = canonical_json_bytes({
            "encoding_abi": ENCODING_ABI,
            "encoding_projection_sha256": ENCODING_PROJECTION_SHA256,
            "release": RELEASE,
        })
        with tempfile.TemporaryDirectory() as directory:
            elf = pathlib.Path(directory) / "case.o"
            make_note_elf(elf, descriptor)
            note = parse_pto_isa_note(elf)
            self.assertEqual(note.release, RELEASE)
            make_note_elf(elf, descriptor, duplicate=True)
            with self.assertRaisesRegex(PTOISANoteError, "exactly one"):
                parse_pto_isa_note(elf)

    def test_pto_note_rejects_mismatch_noncanonical_and_flags(self) -> None:
        valid = {
            "encoding_abi": ENCODING_ABI,
            "encoding_projection_sha256": ENCODING_PROJECTION_SHA256,
            "release": RELEASE,
        }
        with tempfile.TemporaryDirectory() as directory:
            elf = pathlib.Path(directory) / "case.o"
            bad = dict(valid, release="0.58.4")
            make_note_elf(elf, canonical_json_bytes(bad))
            with self.assertRaisesRegex(PTOISANoteError, "release identity"):
                parse_pto_isa_note(elf)
            make_note_elf(elf, canonical_json_bytes(valid) + b"\0")
            with self.assertRaisesRegex(PTOISANoteError, "UTF-8 JSON"):
                parse_pto_isa_note(elf)
            make_note_elf(elf, b'{"release": "0.58.5"}')
            with self.assertRaisesRegex(PTOISANoteError, "canonical compact"):
                parse_pto_isa_note(elf)
            make_note_elf(elf, canonical_json_bytes(valid), note_flags=0)
            with self.assertRaisesRegex(PTOISANoteError, "SHF_ALLOC"):
                parse_pto_isa_note(elf)

    def test_committed_cases_validate_and_select_by_explicit_pto_id(self) -> None:
        root = pathlib.Path(__file__).parents[1] / "avs" / "cases"
        cases = _load_cases(root)
        self.assertEqual(
            sorted(cases),
            [
                "block_64_stop_pc", "host_exit_group", "scalar-c-return",
                "scalar-ir-return", "scalar_stop_pc", "tile_tadd_stop_pc",
            ],
        )
        selected, obligations = _select_cases(cases, ["PTO-INST-TILE-TADD"], [])
        self.assertEqual(selected, ["tile_tadd_stop_pc"])
        self.assertIn("ASLMODEL-VERIF-TILE-TADD-STOP-PC-001", obligations)
        with self.assertRaisesRegex(ValueError, "no AVS case"):
            _select_cases(cases, ["PTO-UNKNOWN"], [])
        with self.assertRaisesRegex(
            ValueError, "PTO-UNKNOWN-A, PTO-UNKNOWN-B"
        ):
            _select_cases(cases, ["PTO-UNKNOWN-A", "PTO-UNKNOWN-B"], [])

        generated = copy.deepcopy(cases["scalar-c-return"][1])
        generated["golden"]["provenance"]["model_generated"] = True  # type: ignore[index]
        with self.assertRaisesRegex(ValueError, "model_generated=false"):
            validate_case(generated)

    def test_real_ndf_impact_output_selects_changed_instruction_ids(self) -> None:
        document = {
            "schema_version": "0.1", "command": "impact pto-release",
            "ok": True, "diagnostics": [],
            "data": {
                "schema_version": "1", "compatibility": "breaking",
                "conformance_targets": [{
                    "consumer_uri": "ndf://asl-model/ASLMODEL-REQ-SCALAR-STOP-PC-001",
                    "target_uri": "ndf://pto-spec/PTO-INST-SCALAR-SW-PCR",
                }],
                "changes": [{
                    "uri": "ndf://pto-spec/PTO-INST-SCALAR-SW-PCR",
                    "kind": "modified", "facets": ["instruction-semantics"],
                }, {
                    "uri": "ndf://pto-spec/PTO-INST-TILE-RETIRED",
                    "kind": "removed", "facets": ["instruction-semantics"],
                }],
                "affected_consumers": [], "absence_watches": [],
                "presence_changes": [], "deterministic_actions": [],
                "human_decisions": [],
            },
        }
        with tempfile.TemporaryDirectory() as directory:
            path = pathlib.Path(directory) / "impact.json"
            path.write_text(json.dumps(document), encoding="utf-8")
            report, digest, affected = _load_impact(path)
        self.assertEqual(affected, ["PTO-INST-SCALAR-SW-PCR"])
        self.assertEqual(digest, canonical_sha256(report))

    def test_runtime_model_lock_uses_candidate_not_committed_baseline(self) -> None:
        candidate = repository("e" * 40)
        candidate["repository"] = "https://github.com/PTO-ISA/pto-spec.git"
        aslref = repository(ASLREF_COMMIT)
        aslref["repository"] = "https://github.com/PTO-ISA/herdtools7.git"
        runtime = _model_run_lock(identity(), candidate, aslref)
        committed = json.loads(
            (pathlib.Path(__file__).parents[1] / "pto-lock.json").read_text(encoding="utf-8")
        )
        self.assertEqual(runtime["pto_commit"], "e" * 40)
        self.assertNotEqual(runtime["pto_commit"], committed["pto_commit"])


if __name__ == "__main__":
    unittest.main()
