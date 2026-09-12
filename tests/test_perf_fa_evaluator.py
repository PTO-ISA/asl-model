import unittest
from types import SimpleNamespace

from tools.perf_fa_evaluator import (
    EXPECTED_FAULT_CODE,
    EXPECTED_FAULT_PC,
    EXPECTED_FA_STEPS,
    EXPECTED_TRACE_SHA256,
    _parallel_execution_matches,
    _signature_matches,
    _trace_sha256,
)


def step(index: int):
    return SimpleNamespace(
        pe_id=index % 4,
        thread_id=index % 4,
        address=0x1000 + index * 2,
        instruction=5,
        length_bits=16,
        status="committed",
        returncode=0,
        next_pc=0x1002 + index * 2,
        finished=False,
        fault_code=None,
    )


class FaPerformanceEvaluatorTest(unittest.TestCase):
    def test_trace_hash_covers_every_semantic_step_field(self):
        original = [step(0), step(1)]
        changed = [step(0), step(1)]
        changed[1].next_pc += 2

        self.assertNotEqual(_trace_sha256(original), _trace_sha256(changed))

    def test_signature_requires_exact_fault_and_trace(self):
        steps = [step(index) for index in range(EXPECTED_FA_STEPS)]
        steps[-1].address = EXPECTED_FAULT_PC
        steps[-1].fault_code = EXPECTED_FAULT_CODE
        steps[-1].status = "rejected"
        steps[-1].returncode = 1
        result = SimpleNamespace(steps=steps, termination="step_failed")

        self.assertTrue(_signature_matches(result, EXPECTED_TRACE_SHA256))
        self.assertFalse(_signature_matches(result, "0" * 64))
        steps[-1].fault_code = None
        self.assertFalse(_signature_matches(result, EXPECTED_TRACE_SHA256))

    def test_parallel_gate_requires_attempt_without_fallback(self):
        result = SimpleNamespace(
            runtime_metrics={
                "parallel": {
                    "attempted": True,
                    "fallback": False,
                    "rounds": 2,
                    "commits": 2,
                }
            }
        )

        self.assertTrue(_parallel_execution_matches(result, final_round_faults=False))
        result.runtime_metrics["parallel"]["attempted"] = False
        self.assertFalse(_parallel_execution_matches(result, final_round_faults=False))
        result.runtime_metrics["parallel"]["attempted"] = True
        result.runtime_metrics["parallel"]["fallback"] = True
        self.assertFalse(_parallel_execution_matches(result, final_round_faults=False))

    def test_parallel_gate_accounts_for_faulting_final_round(self):
        result = SimpleNamespace(
            runtime_metrics={
                "parallel": {
                    "attempted": True,
                    "fallback": False,
                    "rounds": 82,
                    "commits": 81,
                }
            }
        )

        self.assertTrue(_parallel_execution_matches(result, final_round_faults=True))
        self.assertFalse(_parallel_execution_matches(result, final_round_faults=False))
        result.runtime_metrics["parallel"]["commits"] = 80
        self.assertFalse(_parallel_execution_matches(result, final_round_faults=True))


if __name__ == "__main__":
    unittest.main()
