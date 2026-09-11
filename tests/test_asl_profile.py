import unittest

from asl_model.runtime.profile import AslModelProfile, materialize_profile


class AslProfileTest(unittest.TestCase):
    def test_portable_profile_preserves_all_switches(self):
        source = (
            "config PTO_MODEL_LINX_RUNTIME_COMPAT : boolean = FALSE;\n"
            "config PTO_MODEL_ALLOW_SYSTEM_OPS_IN_NON_SYS_BLOCK : boolean = FALSE;\n"
            "config PTO_MODEL_HOST_MEMORY : boolean = FALSE;\n"
            "config PTO_MODEL_FRAME_SP_INDEX : integer {0..31} = 1;\n"
            "config PTO_MODEL_LINX_LEGACY_C_BSTOP : boolean = FALSE;\n"
            "config PTO_MODEL_LINX_TRACE_BOUNDARY_COMPAT : boolean = FALSE;\n"
            "config PTO_MODEL_MSET_MAX_BYTES : integer {63..262144} = 262144;\n"
        )
        self.assertEqual(materialize_profile(source, "portable"), source)

    def test_linx_profile_requires_explicit_selection_and_sets_all_runtime_switches(self):
        source = (
            "config PTO_MODEL_LINX_RUNTIME_COMPAT : boolean = FALSE;\n"
            "config PTO_MODEL_ALLOW_SYSTEM_OPS_IN_NON_SYS_BLOCK : boolean = FALSE;\n"
            "config PTO_MODEL_HOST_MEMORY : boolean = FALSE;\n"
            "config PTO_MODEL_FRAME_SP_INDEX : integer {0..31} = 1;\n"
            "config PTO_MODEL_LINX_LEGACY_C_BSTOP : boolean = FALSE;\n"
            "config PTO_MODEL_LINX_TRACE_BOUNDARY_COMPAT : boolean = FALSE;\n"
            "config PTO_MODEL_MSET_MAX_BYTES : integer {63..262144} = 262144;\n"
        )
        projected = materialize_profile(source, "linx-runtime")
        self.assertEqual(projected.count("= TRUE"), 5)
        self.assertIn("PTO_MODEL_FRAME_SP_INDEX : integer {0..31} = 1", projected)
        self.assertIn("PTO_MODEL_MSET_MAX_BYTES : integer {63..262144} = 262144", projected)

    def test_unknown_profile_is_rejected(self):
        with self.assertRaises(ValueError):
            AslModelProfile.select("implicit")


if __name__ == "__main__":
    unittest.main()
