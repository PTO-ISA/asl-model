import struct
import tempfile
import unittest
import os
from pathlib import Path

from asl_model.runtime.asl_elf import AslElfRunner
from asl_model.runtime.elf import ElfFormatError, ElfLoader
from asl_model.runtime.protocol import ElfLoadRequest

SPEC_ROOT = Path(os.environ.get("PTO_SPEC_ROOT", "/nonexistent/pto-spec"))
HAS_SPEC = (SPEC_ROOT / "build" / "pto-spec.asl").is_file() and (SPEC_ROOT / "scripts" / "aslref").is_file()


class ElfLoaderTest(unittest.TestCase):
    @staticmethod
    def _write_minimal(path: Path, machine: int) -> None:
        entry = 0x1000
        code = b"\x05\x00\x00\x00"
        header = struct.pack(
            "<16sHHIQQQIHHHHHH",
            b"\x7fELF" + bytes([2, 1, 1]) + bytes(9),
            2, machine, 1, entry, 64, 0, 0, 64, 56, 1, 0, 0, 0,
        )
        program = struct.pack("<IIQQQQQQ", 1, 5, 120, entry, entry, len(code), len(code), 0x1000)
        path.write_bytes(header + program + bytes(120 - len(header) - len(program)) + code)

    def test_loads_one_elf64_load_segment(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "mini.elf"
            entry = 0x1000
            code = b"\x05\x00\x00\x00"
            phoff = 64
            header = struct.pack(
                "<16sHHIQQQIHHHHHH",
                b"\x7fELF" + bytes([2, 1, 1]) + bytes(9),
                2, 0xF3, 1, entry, phoff, 0, 0, 64, 56, 1, 0, 0, 0,
            )
            program = struct.pack("<IIQQQQQQ", 1, 5, 120, entry, entry, len(code), len(code), 0x1000)
            path.write_bytes(header + program + bytes(120 - len(header) - len(program)) + code)
            image = ElfLoader().load(ElfLoadRequest(path=path))
            self.assertEqual(image.entry_point, entry)
            self.assertEqual(image.segments[0].data, code)
            self.assertEqual(image.segments[0].permissions, "rx")

    def test_load_can_relocate_image_and_symbols(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "mini.elf"
            entry = 0x1000
            code = b"\x05\x00\x00\x00"
            header = struct.pack(
                "<16sHHIQQQIHHHHHH",
                b"\x7fELF" + bytes([2, 1, 1]) + bytes(9),
                2, 0xF3, 1, entry, 64, 0, 0, 64, 56, 1, 0, 0, 0,
            )
            program = struct.pack("<IIQQQQQQ", 1, 5, 120, entry, entry, len(code), len(code), 0x1000)
            path.write_bytes(header + program + bytes(120 - len(header) - len(program)) + code)
            image = ElfLoader().load(ElfLoadRequest(path=path, requested_base=0))
            self.assertEqual(image.entry_point, 0)
            self.assertEqual(image.segments[0].address, 0)

    def test_expected_machine_accepts_pto_machine_and_reports_it(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "pto.elf"
            self._write_minimal(path, 0xE9)
            image = ElfLoader().load(ElfLoadRequest(path=path, expected_machine=0xE9))
            self.assertEqual(image.metadata["machine"], 0xE9)
            self.assertEqual(image.metadata["pto_isa_note_status"], "not-provided")

    def test_expected_machine_rejects_mismatch_with_both_values(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "wrong-machine.elf"
            self._write_minimal(path, 0x105)
            with self.assertRaisesRegex(ElfFormatError, r"expected 0xe9, found 0x105"):
                ElfLoader().load(ElfLoadRequest(path=path, expected_machine=0xE9))


    def test_asl_elf_runner_uses_asl_decoder_for_width(self):
        if not HAS_SPEC:
            self.skipTest("generated PTO ASL artifact is unavailable")
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "mini.elf"
            entry = 0x1000
            code = b"\x05\x00\x00\x00\x05\x00\x00\x00"
            header = struct.pack(
                "<16sHHIQQQIHHHHHH",
                b"\x7fELF" + bytes([2, 1, 1]) + bytes(9),
                2, 0xF3, 1, entry, 64, 0, 0, 64, 56, 1, 0, 0, 0,
            )
            program = struct.pack("<IIQQQQQQ", 1, 5, 120, entry, entry, len(code), len(code), 0x1000)
            path.write_bytes(header + program + bytes(120 - len(header) - len(program)) + code)
            runner = AslElfRunner(SPEC_ROOT)
            result = runner.run(path, length_bits=None, max_instructions=2)
            # The bound is not a pass: the run never reached a terminal event.
            self.assertEqual(result.as_dict()["status"], "unfinished")
            self.assertFalse(result.complete)
            self.assertEqual(result.steps[0].length_bits, 32)
            self.assertEqual(len(result.steps), 2)
            self.assertEqual(result.steps[1].address, entry + 4)
            layout = result.as_dict()["runtime_layout"]
            self.assertEqual(layout["stack_policy"], "after-image")
            self.assertEqual(layout["pe_count"], 1)
            self.assertEqual(layout["image_range"], {"start": entry, "end": entry + len(code)})
            self.assertEqual(layout["stack_banks"][0]["pe_id"], 0)


if __name__ == "__main__":
    unittest.main()
