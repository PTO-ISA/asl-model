import json
import math
import unittest
from pathlib import Path

from asl_model.state import (
    ArchitectureState,
    ExecutionResult,
    FaultState,
    MemoryEffect,
    MemoryRegion,
    MemoryState,
    ScalarState,
    StateEnvelope,
    TileDescriptor,
    TileState,
    TileValue,
    canonical_hash,
    canonical_json,
    state_diff,
)


ROOT = Path(__file__).resolve().parents[1]


class AslStateSchemaTest(unittest.TestCase):
    def test_schema_is_versioned_and_covers_architecture_domains(self):
        schema = json.loads(
            (ROOT / "src" / "pto_asl_model" / "architecture_state.schema.json").read_text(encoding="utf-8")
        )
        self.assertEqual(schema["$schema"], "https://json-schema.org/draft/2020-12/schema")
        self.assertEqual(schema["$defs"]["state"]["required"], [
            "scalar", "block", "tile", "shared", "memory", "fault",
            "pe_id", "thread_id", "cycle", "extensions",
        ])
        for domain in ("scalar", "block", "tile", "shared", "memory", "fault"):
            self.assertIn(domain, schema["$defs"])

    def test_canonical_serialization_is_order_independent(self):
        left = {"z": [2, 1], "a": {"b": 2, "a": 1}}
        right = {"a": {"a": 1, "b": 2}, "z": [2, 1]}
        self.assertEqual(canonical_json(left), canonical_json(right))
        self.assertEqual(canonical_hash(left), canonical_hash(right))
        with self.assertRaises(ValueError):
            canonical_json({"bad": math.nan})

    def test_state_round_trip_preserves_tile_memory_and_extensions(self):
        state = ArchitectureState(
            scalar=ScalarState(registers={"x1": 7}, pc=0x100, tpc=0x200),
            tile=TileState(
                registers={
                    "t0": TileValue(
                        descriptor=TileDescriptor(
                            dtype="fp16", layout="row_major", shape=[2, 2],
                            valid_shape=[1, 2], strides=[2, 1],
                        ),
                        data=[1, 2, 0, 0], defined=[True, True, False, False],
                        generation=3,
                    )
                }
            ),
            memory=MemoryState(
                cells={"0x1000": 42}, regions=[MemoryRegion(0x1000, 16, "rw", "input")]
            ),
            fault=FaultState(),
            extensions={"future.domain": {"version": 2}},
        )
        restored = ArchitectureState.from_dict(state.as_dict())
        self.assertEqual(restored.as_dict(), state.as_dict())
        self.assertEqual(restored.sha256(), state.sha256())

    def test_initial_and_result_envelopes_are_deterministic(self):
        initial = StateEnvelope.initial(ArchitectureState(), artifact={"commit": "abc"})
        result = ExecutionResult(
            instruction="ADD",
            status="committed",
            initial_state=initial,
            final_state=ArchitectureState(scalar=ScalarState(pc=4)),
            memory_effects=[MemoryEffect("write", 0x1000, 4, [1, 2, 3, 4])],
            pc_before=0,
            pc_after=4,
        )
        payload = result.as_dict()
        self.assertEqual(payload["schema"], "pto.asl-model.execution-result.v1")
        self.assertEqual(payload["kind"], "execution_result")
        self.assertEqual(payload["initial_state"]["kind"], "initial_state")
        self.assertEqual(result.sha256(), canonical_hash(payload))
        restored = ExecutionResult.from_dict(payload)
        self.assertEqual(restored.as_dict(), payload)

    def test_state_diff_only_reports_changed_domains(self):
        before = ArchitectureState()
        after = ArchitectureState(scalar=ScalarState(pc=4))
        diff = state_diff(before, after)
        self.assertEqual(set(diff), {"scalar"})
        self.assertEqual(diff["scalar"]["before"]["pc"], 0)
        self.assertEqual(diff["scalar"]["after"]["pc"], 4)


if __name__ == "__main__":
    unittest.main()
