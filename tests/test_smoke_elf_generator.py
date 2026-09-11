import json
import tempfile
import unittest
from pathlib import Path

from asl_model.runtime.elf import ElfLoader
from asl_model.runtime.protocol import ElfLoadRequest
from tools.generate_smoke_elf import build


class SmokeElfGeneratorTest(unittest.TestCase):
    def test_builds_carrier_from_catalog_owned_encoding(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            catalog = root / "spec" / "catalog" / "scalar-forms.json"
            catalog.parent.mkdir(parents=True)
            catalog.write_text(json.dumps({
                "forms": [{
                    "mnemonic": "ADD",
                    "status": "accepted",
                    "constraints": [],
                    "form_id": "add-test-form",
                    "encoding": [{
                        "width_bits": 32,
                        "mask": "0x0000707f",
                        "match": "0x00000005",
                    }],
                }],
            }), encoding="utf-8")
            output = root / "smoke.elf"

            result = build(root, output)
            image = ElfLoader().load(
                ElfLoadRequest(path=output, expected_machine=0xE9)
            )

            self.assertEqual(result["form_id"], "add-test-form")
            self.assertEqual(result["width_bits"], 32)
            self.assertEqual(image.entry_point, 0x1000)
            self.assertEqual(image.segments[0].data, bytes.fromhex("05000000"))


if __name__ == "__main__":
    unittest.main()
