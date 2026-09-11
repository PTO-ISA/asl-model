import json
import subprocess
import sys
import unittest
import os
from pathlib import Path

from asl_model.backend import (
    BackendAvailability,
    BackendResult,
    DifferentialBackend,
    NativeBackend,
)
from asl_model.cases import ModelCase


ROOT = Path(__file__).resolve().parents[1]
SPEC_ROOT = Path(os.environ.get("PTO_SPEC_ROOT", "/nonexistent/pto-spec"))
HAS_SPEC = (SPEC_ROOT / "build" / "pto-spec.asl").is_file() and (SPEC_ROOT / "scripts" / "aslref").is_file()


def case() -> ModelCase:
    return ModelCase(
        case_id="test.case",
        instruction="TEST",
        instruction_class="test",
        source=Path("asl/test.asl"),
        test=Path("tests/asl/test.asl"),
    )


class ComparableBackend:
    def __init__(self, name: str, observations: dict[str, object], artifact=None):
        self.name = name
        self._observations = observations
        self._artifact = artifact or {"spec": "same"}

    def availability(self):
        return BackendAvailability(self.name, True)

    def execute(self, requested_case):
        return BackendResult(
            backend=self.name,
            case_id=requested_case.case_id,
            instruction=requested_case.instruction,
            status="passed",
            returncode=0,
            artifact=self._artifact,
            observations=self._observations,
            comparable=True,
        )


class BackendInterfaceTest(unittest.TestCase):
    def test_native_stub_is_explicitly_unavailable(self):
        result = NativeBackend().execute(case())
        self.assertEqual(result.status, "unavailable")
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(result.succeeded)
        self.assertFalse(NativeBackend().availability().available)

    def test_differential_match_requires_comparable_snapshots(self):
        backend = DifferentialBackend(
            ComparableBackend("asl", {"gpr": [1, 2]}),
            ComparableBackend("native", {"gpr": [1, 2]}),
        )
        result = backend.execute(case())
        self.assertEqual(result.status, "passed")
        self.assertEqual(result.returncode, 0)

    def test_differential_mismatch_is_not_a_pass(self):
        backend = DifferentialBackend(
            ComparableBackend("asl", {"gpr": [1, 2]}),
            ComparableBackend("native", {"gpr": [1, 3]}),
        )
        result = backend.execute(case())
        self.assertEqual(result.status, "mismatch")
        self.assertNotEqual(result.returncode, 0)

    def test_differential_without_snapshots_is_inconclusive(self):
        backend = DifferentialBackend(
            ComparableBackend("asl", {}, artifact={"spec": "same"}),
            NativeBackend(
                executor=lambda requested_case: BackendResult(
                    backend="native",
                    case_id=requested_case.case_id,
                    instruction=requested_case.instruction,
                    status="passed",
                    returncode=0,
                    artifact={"spec": "same"},
                )
            ),
        )
        result = backend.execute(case())
        self.assertEqual(result.status, "inconclusive")
        self.assertNotEqual(result.returncode, 0)

    def test_cli_native_check_does_not_claim_ready(self):
        completed = subprocess.run(
            [sys.executable, "-m", "asl_model.cli", "check", "--backend", "native"],
            cwd=ROOT,
            capture_output=True,
            text=True,
        )
        self.assertNotEqual(completed.returncode, 0)
        payload = json.loads(completed.stdout)
        self.assertEqual(payload["status"], "unavailable")
        self.assertFalse(payload["availability"]["available"])

    def test_cli_session_reports_process_backend(self):
        if not HAS_SPEC:
            self.skipTest("generated PTO ASL artifact is unavailable")
        completed = subprocess.run(
            [sys.executable, "-m", "asl_model.cli", "session", "--backend", "asl"],
            cwd=ROOT,
            check=True,
            capture_output=True,
            text=True,
        )
        payload = json.loads(completed.stdout)
        self.assertEqual(payload["status"], "ready")
        self.assertEqual(payload["backend"], "asl-process")
        self.assertIn("pto_spec_commit", payload["artifact"])


if __name__ == "__main__":
    unittest.main()
