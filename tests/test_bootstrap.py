import unittest
from pathlib import Path


class BootstrapContractTest(unittest.TestCase):
    def test_bootstrap_forces_commit_bound_spec_and_aslref_builds(self):
        script = (
            Path(__file__).parents[1] / "scripts" / "bootstrap.sh"
        ).read_text(encoding="utf-8")
        self.assertIn('make -B -C "$spec_dir" build', script)
        self.assertIn('make -C "$spec_dir" setup', script)
        self.assertNotIn('if [[ ! -f "$spec_dir/build/pto-spec.asl"', script)


if __name__ == "__main__":
    unittest.main()
