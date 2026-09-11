import json
import subprocess
import sys
import unittest
import os
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SPEC_ROOT = Path(os.environ.get("PTO_SPEC_ROOT", "/nonexistent/pto-spec"))
HAS_SPEC = (SPEC_ROOT / "build" / "pto-spec.asl").is_file() and (SPEC_ROOT / "scripts" / "aslref").is_file()


class AslModelFrameworkTest(unittest.TestCase):
    @unittest.skipUnless(HAS_SPEC, "generated PTO ASL artifact is unavailable")
    def test_integration_spec_matches_development_lock(self):
        lock = json.loads(
            (ROOT / "dependencies" / "pto-spec.lock").read_text(encoding="utf-8")
        )
        current = subprocess.run(
            ["git", "-C", str(SPEC_ROOT), "rev-parse", "HEAD"],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
        self.assertEqual(current, lock["commit"])

    @unittest.skipUnless(HAS_SPEC, "generated PTO ASL artifact is unavailable")
    def test_registry_check(self):
        result = subprocess.run(
            [sys.executable, "-m", "asl_model.cli", "check"],
            cwd=ROOT,
            check=True,
            capture_output=True,
            text=True,
        )
        payload = json.loads(result.stdout)
        self.assertEqual(payload["status"], "ready")
        self.assertEqual(
            payload["cases"],
            ["scalar.add.integer", "tile.tadd.elements", "tlsu.tload.direct"],
        )

    @unittest.skipUnless(HAS_SPEC, "generated PTO ASL artifact is unavailable")
    def test_manifest_contains_artifact_identity(self):
        result = subprocess.run(
            [sys.executable, "-m", "asl_model.cli", "manifest"],
            cwd=ROOT,
            check=True,
            capture_output=True,
            text=True,
        )
        payload = json.loads(result.stdout)
        self.assertEqual(len(payload["pto_spec_commit"]), 40)
        for field in ("spec_sha256", "decoder_sha256", "source_order_sha256"):
            self.assertEqual(len(payload[field]), 64)


if __name__ == "__main__":
    unittest.main()
