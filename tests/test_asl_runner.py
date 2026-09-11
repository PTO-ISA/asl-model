import json
import tempfile
import unittest
from pathlib import Path
from asl_model.backend import BackendAvailability, BackendResult
from asl_model.cases import CaseRegistry
from asl_model.runner import run_cases


ROOT = Path(__file__).resolve().parents[1]


class FakeBackend:
    name = "fake"

    def availability(self):
        return BackendAvailability(self.name, True)

    def execute(self, case):
        return BackendResult(
            backend=self.name,
            case_id=case.case_id,
            instruction=case.instruction,
            status="passed",
            returncode=0,
            artifact={"spec": "test"},
            observations={"case": case.case_id},
            comparable=True,
        )


class AslRunnerTest(unittest.TestCase):
    def test_batch_report_and_json_output(self):
        registry = CaseRegistry(ROOT / "src" / "asl_model" / "cases.json", ROOT.parent / "pto-spec")
        report = run_cases(FakeBackend(), registry)
        self.assertEqual(report.passed, 3)
        self.assertEqual(report.failed, 0)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "report.json"
            report.write_json(path)
            payload = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(payload["schema"], "pto.asl-model-batch-report.v1")
            self.assertEqual(payload["summary"]["total"], 3)


if __name__ == "__main__":
    unittest.main()
