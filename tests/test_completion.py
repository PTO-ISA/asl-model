import json
import tempfile
import unittest
from pathlib import Path

from asl_model.runtime.completion import AslCompletionPolicy


def _write_catalog(root: Path, record: dict) -> None:
    path = root / "asl" / "scalar" / "sys" / "ACRC.asl"
    path.parent.mkdir(parents=True)
    path.write_text(
        "// PTO-INSTRUCTION: " + json.dumps(record) + "\n",
        encoding="utf-8",
    )


class AslCompletionCatalogTest(unittest.TestCase):
    def test_loads_current_catalog_record_layout(self):
        # This is the layout emitted by the current PTO-SPEC generator:
        # mnemonic is carried by the catalog record, not the outer object.
        record = {
            "assembly": ["acrc rst_type"],
            "catalog_records": [
                {
                    "mnemonic": "ACRC",
                    "encoding": [
                        {"width_bits": 32, "mask": "0xff0fffff", "match": "0x302b"}
                    ],
                }
            ],
        }
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _write_catalog(root, record)
            policy = AslCompletionPolicy(root)

        self.assertEqual(policy.encodings[0].width_bits, 32)
        self.assertTrue(policy.is_terminal(0x302B, 32))

    def test_loads_legacy_outer_record_layout(self):
        record = {
            "mnemonic": "ACRC",
            "encoding": [
                {"width_bits": 32, "mask": "0xff0fffff", "match": "0x302b"}
            ],
        }
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _write_catalog(root, record)
            policy = AslCompletionPolicy(root)

        self.assertTrue(policy.is_terminal(0x302B, 32))

    def test_missing_acrc_encoding_is_reported(self):
        record = {
            "catalog_records": [{"mnemonic": "BSTART", "encoding": []}],
        }
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _write_catalog(root, record)
            with self.assertRaisesRegex(FileNotFoundError, "no ACRC encoding"):
                AslCompletionPolicy(root)


if __name__ == "__main__":
    unittest.main()
