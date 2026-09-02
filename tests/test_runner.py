from __future__ import annotations

import io
import hashlib
import json
import pathlib
import struct
import tempfile
import unittest
from contextlib import redirect_stdout
from unittest import mock

from pto_asl_model.runner import (
    ELF_HEADER,
    PROGRAM_HEADER,
    SECTION_HEADER,
    SYMBOL_ENTRY,
    ElfError,
    RunConfiguration,
    build_harness,
    main,
    parse_elf,
    parse_result,
    specialize_reference_profile,
    _load_sidecar,
    _validate_sidecar,
)


def make_elf(path: pathlib.Path, *, machine: int = 0xE9,
             address: int = 0x100, payload: bytes = b"\x16\x00") -> None:
    identification = bytearray(16)
    identification[:4] = b"\x7fELF"
    identification[4] = 2
    identification[5] = 1
    identification[6] = 1
    program_offset = ELF_HEADER.size
    file_offset = ELF_HEADER.size + PROGRAM_HEADER.size
    header = ELF_HEADER.pack(
        bytes(identification), 2, machine, 1, address, program_offset, 0, 0,
        ELF_HEADER.size, PROGRAM_HEADER.size, 1, 0, 0, 0,
    )
    program = PROGRAM_HEADER.pack(
        1, 5, file_offset, address, address, len(payload), len(payload) + 2, 1,
    )
    path.write_bytes(header + program + payload)


def make_symbol_elf(path: pathlib.Path) -> None:
    identification = bytearray(16)
    identification[:4] = b"\x7fELF"
    identification[4:7] = b"\x02\x01\x01"
    program_offset = ELF_HEADER.size
    file_offset = ELF_HEADER.size + PROGRAM_HEADER.size
    payload = b"\x16\x00\x00\x00"
    strings = (
        b"\0main\0cross_model_stop\0cross_model_result\0"
        b"cross_model_result_size\0"
    )
    offsets = {
        "main": strings.index(b"main"),
        "cross_model_stop": strings.index(b"cross_model_stop"),
        "cross_model_result": strings.index(b"cross_model_result"),
        "cross_model_result_size": strings.index(b"cross_model_result_size"),
    }
    symbol_offset = file_offset + len(payload)
    symbols = b"".join([
        SYMBOL_ENTRY.pack(0, 0, 0, 0, 0, 0),
        SYMBOL_ENTRY.pack(offsets["main"], 0x12, 0, 1, 0x100, 0),
        SYMBOL_ENTRY.pack(offsets["cross_model_stop"], 0x10, 0, 1, 0x102, 0),
        SYMBOL_ENTRY.pack(offsets["cross_model_result"], 0x11, 0, 1, 0x200, 8192),
        SYMBOL_ENTRY.pack(
            offsets["cross_model_result_size"], 0x10, 0, 0xFFF1, 8192, 0
        ),
    ])
    string_offset = symbol_offset + len(symbols)
    section_offset = string_offset + len(strings)
    sections = b"".join([
        SECTION_HEADER.pack(0, 0, 0, 0, 0, 0, 0, 0, 0, 0),
        SECTION_HEADER.pack(
            0, 2, 0, 0, symbol_offset, len(symbols), 2, 1, 8, SYMBOL_ENTRY.size
        ),
        SECTION_HEADER.pack(0, 3, 0, 0, string_offset, len(strings), 0, 0, 1, 0),
    ])
    header = ELF_HEADER.pack(
        bytes(identification), 2, 0xE9, 1, 0x100, program_offset,
        section_offset, 0, ELF_HEADER.size, PROGRAM_HEADER.size, 1,
        SECTION_HEADER.size, 3, 0,
    )
    program = PROGRAM_HEADER.pack(
        1, 7, file_offset, 0x100, 0x100, len(payload), 0x2200, 1,
    )
    path.write_bytes(header + program + payload + symbols + strings + sections)


class RunnerTests(unittest.TestCase):
    def test_quiet_cli_suppresses_manifest_stdout(self) -> None:
        with mock.patch(
            "pto_asl_model.runner.run", return_value={"status": "passed"}
        ) as run_mock:
            output = io.StringIO()
            with redirect_stdout(output):
                status = main([
                    "--asl-spec", "spec.asl",
                    "--aslref", "aslref",
                    "--elf", "case.elf",
                    "--lock", "pto-lock.json",
                    "--quiet",
                ])
        self.assertEqual(status, 0)
        self.assertEqual(output.getvalue(), "")
        self.assertEqual(
            run_mock.call_args.args[0].memory_backend, "host-sparse"
        )

    def test_host_memory_runner_owns_only_physical_storage(self) -> None:
        source = (
            pathlib.Path(__file__).parents[1]
            / "src" / "pto_asl_model" / "aslref_host_memory.ml"
        ).read_text(encoding="utf-8")
        self.assertIn('"ReadPhysicalMemoryByte"', source)
        self.assertIn('"WritePhysicalMemoryByte"', source)
        self.assertNotIn("DecodeScalar", source)
        self.assertNotIn("ExecutePTOInstruction", source)

    def test_parse_static_elf(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = pathlib.Path(directory) / "case.elf"
            make_elf(path)
            image = parse_elf(path)
            self.assertEqual(image.entry, 0x100)
            self.assertEqual(image.segments[0].data, b"\x16\x00")
            self.assertEqual(image.segments[0].memory_size, 4)

    def test_rejects_wrong_machine(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = pathlib.Path(directory) / "case.elf"
            make_elf(path, machine=1)
            with self.assertRaisesRegex(ElfError, "machine"):
                parse_elf(path)

    def test_high_address_uses_explicit_hosted_memory_bound(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = pathlib.Path(directory) / "case.elf"
            make_elf(path, address=0x111AC)
            with self.assertRaisesRegex(ElfError, "reference memory"):
                parse_elf(path)
            image = parse_elf(path, memory_bytes=0x20000)
            self.assertEqual(image.entry, 0x111AC)

    def test_specializes_reference_memory_bound(self) -> None:
        source = (
            b"config PTO_MODEL_MEMORY_BYTES : integer {256..65536} = 4096;\n"
            b"config PTO_MODEL_TILE_ELEMENTS : integer {1..32768} = 32768;\n"
        )
        self.assertEqual(
            specialize_reference_profile(source, 0x20000, 1),
            b"config PTO_MODEL_MEMORY_BYTES : integer {256..131072} = 131072;\n"
            b"config PTO_MODEL_TILE_ELEMENTS : integer {1..32768} = 1;\n",
        )

    def test_fresh_process_reset_skips_only_memory_sweep(self) -> None:
        source = (
            b"config PTO_MODEL_MEMORY_BYTES : integer {256..65536} = 4096;\n"
            b"config PTO_MODEL_TILE_ELEMENTS : integer {1..32768} = 32768;\n"
            b"implementation func ResetProfileState()\n"
            b"begin\n"
            b"    for index = 0 to PTO_MODEL_MEMORY_BYTES - 1 do\n"
            b"        _Memory[[index]] = Zeros{8};\n"
            b"    end;\n"
            b"    ResetBundleControlState();\n"
            b"end;\n"
        )
        specialized = specialize_reference_profile(
            source, 0x20000, 1, fresh_process_reset=True
        )
        self.assertNotIn(b"_Memory[[index]] = Zeros{8}", specialized)
        self.assertIn(b"ResetBundleControlState();", specialized)
        self.assertIn(b"fresh ASLRef process", specialized)

    def test_fresh_process_reset_fails_closed_on_source_drift(self) -> None:
        source = (
            b"config PTO_MODEL_MEMORY_BYTES : integer {256..65536} = 4096;\n"
            b"config PTO_MODEL_TILE_ELEMENTS : integer {1..32768} = 32768;\n"
        )
        with self.assertRaisesRegex(ValueError, "memory reset loop"):
            specialize_reference_profile(
                source, 0x20000, 1, fresh_process_reset=True
            )

    def test_runtime_typecheck_mode_is_explicit(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = pathlib.Path(directory) / "case.elf"
            make_elf(path)
            image = parse_elf(path)
            configuration = RunConfiguration(
                asl_spec=path,
                aslref=path,
                elf=path,
                stop_pc=0x102,
                max_steps=4,
                result_address=0,
                result_size=0,
                runtime_typecheck="minimal",
            )
            self.assertIn("ExecuteNextPTOInstruction()", build_harness(image, configuration))

    def test_harness_executes_consecutive_steps(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = pathlib.Path(directory) / "case.elf"
            make_elf(path)
            image = parse_elf(path)
            harness = build_harness(image, RunConfiguration(
                asl_spec=path,
                aslref=path,
                elf=path,
                stop_pc=0x102,
                max_steps=4,
                result_address=0x100,
                result_size=2,
            ))
            self.assertIn("ExecuteNextPTOInstruction()", harness)
            self.assertIn("for model_step = 0 to 3", harness)
            self.assertIn("model_stop_hits == 1", harness)
            self.assertIn("PTO_FAULT_STATUS", harness)
            self.assertIn("PTO_FAULT_ADDRESS", harness)
            self.assertIn("PTO_PREVIOUS_TPC", harness)
            self.assertIn("PTO_PREVIOUS_PREVIOUS_TPC", harness)
            self.assertIn("PTO_DIAG_SP", harness)
            self.assertIn("PTO_DIAG_RA", harness)
            self.assertIn("PTO_DIAG_RETURN_ADDRESS", harness)
            self.assertIn("PTO_DIAG_BINDINGS_COMPLETE", harness)
            self.assertIn("PTO_DIAG_GPR_STRIDE", harness)
            self.assertIn("PTO_DIAG_DATR_TYPE", harness)
            self.assertIn("PTO_DIAG_TILE_OPERANDS_LEGAL", harness)
            self.assertIn("PTO_DIAG_TILE_OPERATION_SELECTED", harness)
            self.assertIn(
                "if diagnostic_tile_operation then\n"
                "                    println \"PTO_DIAG_DATA_TYPE_CODE \",",
                harness,
            )
            self.assertNotIn("ExecutePTOInstruction(", harness)

    def test_stop_policy_can_require_a_later_pc_hit(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = pathlib.Path(directory) / "case.elf"
            make_elf(path)
            image = parse_elf(path)
            harness = build_harness(image, RunConfiguration(
                asl_spec=path,
                aslref=path,
                elf=path,
                stop_pc=0x102,
                stop_after_hits=2,
                max_steps=4,
                result_address=0,
                result_size=0,
            ))
            self.assertIn("model_stop_hits == 2", harness)

    def test_direct_boot_initializes_start_and_return_pc(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = pathlib.Path(directory) / "case.elf"
            make_elf(path, payload=b"\x16\x00\x00\x00")
            image = parse_elf(path)
            harness = build_harness(image, RunConfiguration(
                asl_spec=path,
                aslref=path,
                elf=path,
                stop_pc=0x102,
                start_pc=0x102,
                return_pc=0x104,
                stack_top=0x108,
                max_steps=4,
                result_address=0,
                result_size=0,
            ))
            self.assertIn("WriteTPC(Zeros{PTO_XLEN} + 0x102)", harness)
            self.assertIn("_ReturnAddress = Zeros{PTO_XLEN} + 0x104", harness)
            self.assertIn(
                "WritePEGPR(0, 10, Zeros{PTO_XLEN} + 0x104)", harness
            )
            self.assertIn(
                "WritePEGPR(0, 1, Zeros{PTO_XLEN} + 0x108)", harness
            )
            self.assertIn("PTO_DIAG_STACK_RA", harness)
            self.assertIn("PTO_DIAG_STACK_S0", harness)

    def test_verified_sidecar_resolves_elf_symbols(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            elf = root / "case.elf"
            golden = root / "case.golden.bin"
            sidecar = root / "case.sidecar.json"
            make_symbol_elf(elf)
            golden.write_bytes(bytes(8192))
            image = parse_elf(elf)
            document = {
                "schema": "pto-asl-elf-sidecar-v1",
                "case_id": "scalar.case",
                "identity": {"pto_commit": "pto-commit"},
                "elf": {
                    "path": elf.name,
                    "sha256": image.sha256,
                    "machine": 0xE9,
                    "entry": image.entry,
                    "segments": [{
                        "address": 0x100,
                        "filesz": 4,
                        "memsz": 0x2200,
                        "flags": 7,
                    }],
                },
                "model": {
                    "profile": "bounded-reference-v1",
                    "pe_count": 1,
                    "memory_bytes": 65536,
                    "tile_elements": 1,
                    "runtime_typecheck": "minimal",
                },
                "start": {
                    "symbol": "main",
                    "pc": 0x100,
                    "return_symbol": "cross_model_stop",
                    "return_pc": 0x102,
                },
                "execution": {
                    "stop_symbol": "cross_model_stop",
                    "stop_pc": 0x102,
                    "stop_after_hits": 1,
                    "max_steps": 8,
                    "stack_top": 0x4000,
                },
                "result": {
                    "symbol": "cross_model_result",
                    "size_symbol": "cross_model_result_size",
                    "address": 0x200,
                    "size": 8192,
                    "segments": [{
                        "offset": 0,
                        "size": 8192,
                        "dtype": "opaque-bytes",
                        "shape": [8192],
                        "comparison": "exact",
                    }],
                    "golden": {
                        "path": golden.name,
                        "sha256": hashlib.sha256(golden.read_bytes()).hexdigest(),
                    },
                },
            }
            sidecar.write_text(json.dumps(document), encoding="utf-8")
            configuration, loaded, digest = _load_sidecar(RunConfiguration(
                asl_spec=elf,
                aslref=elf,
                elf=elf,
                stop_pc=0,
                max_steps=0,
                result_address=0,
                result_size=0,
                sidecar=sidecar,
            ))
            self.assertEqual(configuration.start_pc, 0x100)
            self.assertEqual(configuration.result_size, 8192)
            self.assertEqual(digest, hashlib.sha256(sidecar.read_bytes()).hexdigest())
            assert loaded is not None
            _validate_sidecar(
                configuration, image, {"pto_commit": "pto-commit"}, loaded
            )

    def test_result_markers_are_exact(self) -> None:
        result, final_tpc = parse_result(
            "PTO_RESULT_BYTE 0 25\nPTO_RESULT_BYTE 1 0\nPTO_FINAL_TPC 276\n",
            2,
        )
        self.assertEqual(result, b"\x19\x00")
        self.assertEqual(final_tpc, 276)


if __name__ == "__main__":
    unittest.main()
