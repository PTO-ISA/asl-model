import os
import tempfile
import unittest
from pathlib import Path

from pto_asl_model.paths import resolve_pto_spec, repository_root


class PathResolutionTests(unittest.TestCase):
    def test_repository_root_is_model_checkout(self):
        root = repository_root()
        self.assertTrue((root / "CMakeLists.txt").is_file())
        self.assertTrue((root / "src" / "pto_asl_model").is_dir())

    def test_explicit_paths_win_over_environment(self):
        with tempfile.TemporaryDirectory() as directory:
            explicit = Path(directory) / "explicit"
            configured = Path(directory) / "configured"
            old = os.environ.get("PTO_SPEC_ROOT")
            try:
                os.environ["PTO_SPEC_ROOT"] = str(configured)
                self.assertEqual(resolve_pto_spec(explicit), explicit.resolve())
            finally:
                if old is None:
                    os.environ.pop("PTO_SPEC_ROOT", None)
                else:
                    os.environ["PTO_SPEC_ROOT"] = old

if __name__ == "__main__":
    unittest.main()
