import importlib.machinery
import importlib.util
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from pto_asl_model.closure_artifacts import validate_semantic_payload


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "asl-model-elf-smoke"


def _script_module():
    loader = importlib.machinery.SourceFileLoader("asl_model_elf_smoke", str(SCRIPT))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    if spec is None:
        raise RuntimeError("cannot load ELF smoke script")
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


class ElfSmokeScriptTest(unittest.TestCase):
    def test_resolve_spec_accepts_checkout_or_generated_artifact(self):
        smoke = _script_module()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "pto-spec"
            generated = root / "build" / "pto-spec.asl"
            generated.parent.mkdir(parents=True)
            generated.write_text("// generated", encoding="utf-8")
            self.assertEqual(smoke._resolve_spec(root), generated.resolve())
            self.assertEqual(smoke._resolve_spec(generated), generated.resolve())

    def test_resolve_spec_rejects_missing_checkout_artifact(self):
        smoke = _script_module()
        with tempfile.TemporaryDirectory() as directory:
            self.assertIsNone(smoke._resolve_spec(Path(directory)))

    def test_bounded_prefix_is_successful_smoke_but_not_closure_evidence(self):
        smoke = _script_module()
        payload = {
            "status": "passed",
            "complete": False,
            "termination": "max_instructions",
            "pe_count": 1,
            "steps": [{"address": 0x1000, "next_pc": 0x1004}],
        }
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            elf = root / "case.elf"
            spec = root / "pto-spec.asl"
            manifest = root / "smoke.json"
            elf.write_bytes(b"\x7fELF" + bytes(64))
            spec.write_text("// generated", encoding="utf-8")
            completed = mock.Mock(returncode=1, stdout=json.dumps(payload), stderr="")
            with mock.patch.object(
                smoke.subprocess, "run", return_value=completed
            ) as run_mock:
                status = smoke.main([
                    str(elf), "--pto-spec", str(spec),
                    "--parallel-pe-steps",
                    "--manifest-out", str(manifest),
                ])

            self.assertEqual(status, 0)
            result = json.loads(manifest.read_text(encoding="utf-8"))
            self.assertEqual(result["schema"], "pto-asl-model-smoke-v1")
            self.assertEqual(result["validation_level"], "smoke")
            self.assertEqual(result["pto_isa_note_status"], "not-provided")
            self.assertEqual(result["model_lock_status"], "not-provided")
            self.assertEqual(result["sidecar_status"], "not-provided")
            self.assertEqual(result["golden_status"], "not-provided")
            self.assertFalse(result["closure_eligible"])
            command = run_mock.call_args.args[0]
            self.assertIn("--expected-machine", command)
            self.assertEqual(command[command.index("--expected-machine") + 1], "0xe9")
            self.assertIn("--parallel-pe-steps", command)
            with self.assertRaisesRegex(ValueError, "semantic payload"):
                validate_semantic_payload(result)

    def test_failed_or_backend_error_result_remains_nonzero(self):
        smoke = _script_module()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            elf = root / "case.elf"
            spec = root / "pto-spec.asl"
            elf.write_bytes(b"\x7fELF" + bytes(64))
            spec.write_text("// generated", encoding="utf-8")
            for payload in (
                {"status": "failed", "termination": "fault", "steps": []},
                {"status": "backend_error", "error": "worker unavailable"},
            ):
                with self.subTest(status=payload["status"]):
                    completed = mock.Mock(
                        returncode=2, stdout=json.dumps(payload), stderr=""
                    )
                    with mock.patch.object(
                        smoke.subprocess, "run", return_value=completed
                    ):
                        self.assertEqual(
                            smoke.main([str(elf), "--pto-spec", str(spec)]), 1
                        )


if __name__ == "__main__":
    unittest.main()
