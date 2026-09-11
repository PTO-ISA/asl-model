import json
import subprocess
import sys
import unittest
import os
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DEMO = ROOT / "tools" / "asl_backed_demo.py"
MANIFEST = ROOT / "tools" / "generate_asl_model_manifest.py"
SPEC_ROOT = Path(os.environ.get("PTO_SPEC_ROOT", "/nonexistent/pto-spec"))
HAS_SPEC = (SPEC_ROOT / "build" / "pto-spec.asl").is_file() and (SPEC_ROOT / "scripts" / "aslref").is_file()


class AslBackedDemoTest(unittest.TestCase):
    @unittest.skipUnless(HAS_SPEC, "generated PTO ASL artifact is unavailable")
    def test_case_registry_is_ready(self):
        result = subprocess.run(
            [sys.executable, str(DEMO), "--check"],
            cwd=ROOT,
            check=True,
            capture_output=True,
            text=True,
        )
        payload = json.loads(result.stdout)
        self.assertEqual(payload["status"], "ready")
        self.assertEqual(set(payload["cases"]), {"ADD", "TADD", "TLOAD"})

    @unittest.skipUnless(HAS_SPEC, "generated PTO ASL artifact is unavailable")
    def test_manifest_is_derived_from_current_asl(self):
        result = subprocess.run(
            [sys.executable, str(MANIFEST)],
            cwd=ROOT,
            check=True,
            capture_output=True,
            text=True,
        )
        payload = json.loads(result.stdout)
        self.assertEqual(payload["schema"], "pto.asl-model-manifest.v1")
        self.assertGreater(payload["instruction_count"], 0)
        self.assertIn("case_registry_sha256", payload)
        mnemonics = {item["mnemonic"] for item in payload["instructions"]}
        self.assertTrue({"ADD", "TADD", "TLOAD"}.issubset(mnemonics))


if __name__ == "__main__":
    unittest.main()
