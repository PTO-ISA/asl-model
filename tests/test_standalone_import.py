import sys
import tomllib
import unittest
from importlib.util import find_spec
from pathlib import Path


PACKAGE_ROOT = Path(__file__).resolve().parents[1]
SRC = PACKAGE_ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))


class StandaloneImportTests(unittest.TestCase):
    def test_package_import_does_not_need_superproject(self):
        import pto_asl_model

        for name in (
            "AslSession",
            "GuestMemory",
            "HostMemoryBridge",
            "ProgramImage",
            "RuntimeAdapter",
            "RuntimeSnapshot",
            "RuntimeState",
            "InstructionTransaction",
            "SemanticBackend",
        ):
            self.assertFalse(hasattr(pto_asl_model, name))
        self.assertIsNone(find_spec("pto_asl_model.runtime"))
        self.assertEqual(Path(pto_asl_model.__file__).resolve().parent.name, "pto_asl_model")

    def test_public_runner_remains_the_hosted_runner(self):
        import pto_asl_model

        self.assertEqual(pto_asl_model.run.__module__, "pto_asl_model.runner")
        project = tomllib.loads((PACKAGE_ROOT / "pyproject.toml").read_text())
        self.assertEqual(project["project"]["scripts"]["asl-model"], "asl_model.cli:main")

    def test_standalone_runtime_is_a_separate_package(self):
        import asl_model
        import pto_asl_model

        self.assertTrue(hasattr(asl_model, "AslBackend"))
        self.assertEqual(Path(asl_model.__file__).resolve().parent.name, "asl_model")
        self.assertIsNotNone(find_spec("asl_model.runtime"))
        self.assertIsNone(find_spec("pto_asl_model.runtime"))
        self.assertIs(asl_model.ArchitectureState, pto_asl_model.ArchitectureState)
        self.assertIs(asl_model.StateEnvelope, pto_asl_model.StateEnvelope)


if __name__ == "__main__":
    unittest.main()
