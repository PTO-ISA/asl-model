import unittest

from asl_model.runtime.multi_elf import (
    AslMultiPeElfRunner,
    AslWorkerExecutor,
    CallbackFinisher,
    InstructionExecution,
    PeContext,
    UnsupportedPeStateScope,
)
from asl_model.runtime.protocol import ProgramImage, ProgramSegment
from asl_model.runtime.protocol import InstructionRequest
from asl_model.runtime.profile import AslModelProfile


class FakeExecutor:
    name = "fake"

    def __init__(self, fail_pe=None, next_pc=None):
        self.fail_pe = fail_pe
        self.next_pc = next_pc
        self.artifact = {"spec": "test"}
        self.started = None
        self.closed = False

    def start(self, image, contexts):
        self.started = (image, contexts)

    def decode_length(self, context, encoding):
        del context, encoding
        return 16

    def execute(self, context, request):
        del request
        if context.pe_id == self.fail_pe:
            return InstructionExecution("rejected", 1, error="synthetic failure")
        return InstructionExecution("committed", 0, next_pc=self.next_pc)

    def close(self):
        self.closed = True


class ScopedFakeWorker:
    def __init__(self, *_args, **_kwargs):
        self.calls = []
        self.identity = None

    def start(self, source):
        self.calls.append(("start", source))

    def ping(self):
        self.calls.append(("ping",))

    def select_pe(self, pe_id):
        self.calls.append(("select_pe", pe_id))

    def set_tpc(self, value):
        self.calls.append(("set_tpc", value))

    def decode_length(self, _encoding):
        return 16

    def step(self, _instruction, _length_bits):
        self.calls.append(("step",))
        return 0

    def peek_tpc(self):
        return 0x1002

    def stop(self):
        self.calls.append(("stop",))


class CoreScopedFakeWorker(ScopedFakeWorker):
    """Small protocol double for explicit one-VM diagnostics."""

    def __init__(self, *_args, **_kwargs):
        super().__init__(*_args, **_kwargs)
        self.step_count = 0

    def step(self, _instruction, _length_bits):
        self.step_count += 1
        self.calls.append(("step",))
        return 0


class FailingAutoWorker(ScopedFakeWorker):
    def step_auto(self):
        raise RuntimeError("synthetic host failure")


class MultiPeElfRunnerTest(unittest.TestCase):
    def setUp(self):
        self.image = ProgramImage(
            entry_point=0x1000,
            segments=(ProgramSegment(0x1000, b"\x01\x00\x02\x00", 4, "rx"),),
        )

    def test_round_robin_contexts_and_finisher(self):
        executor = FakeExecutor()
        result = AslMultiPeElfRunner("/unused").run_image(
            self.image,
            pe_count=2,
            max_instructions=4,
            executor_factory=lambda: executor,
            finisher=CallbackFinisher(lambda context, request, execution: context.instruction_count >= 1),
        )
        self.assertTrue(result.ok)
        self.assertEqual(result.termination, "all_finished")
        self.assertEqual([(step.pe_id, step.index) for step in result.steps], [(0, 0), (1, 1)])
        self.assertTrue(all(context.finished for context in result.contexts))
        self.assertTrue(executor.closed)
        layout = result.as_dict()["runtime_layout"]
        self.assertEqual(result.as_dict()["schema"], "pto-asl-model-smoke-v1")
        self.assertEqual(result.as_dict()["validation_level"], "smoke")
        self.assertFalse(result.as_dict()["closure_eligible"])
        self.assertEqual(layout["stack_policy"], "after-image")
        self.assertEqual(layout["pe_count"], 2)
        self.assertEqual(len(layout["stack_banks"]), 2)
        self.assertEqual(layout["image_range"], {"start": 0x1000, "end": 0x1004})

    def test_failure_stops_run_and_is_reported(self):
        executor = FakeExecutor(fail_pe=1)
        result = AslMultiPeElfRunner("/unused").run_image(
            self.image,
            pe_count=2,
            max_instructions=4,
            executor_factory=lambda: executor,
        )
        self.assertFalse(result.ok)
        self.assertEqual(result.termination, "step_failed")
        self.assertEqual(result.steps[-1].pe_id, 1)
        self.assertNotEqual(result.as_dict()["status"], "passed")

    def test_out_of_image_before_budget_is_a_failed_smoke(self):
        image = ProgramImage(
            entry_point=0x1000,
            segments=(ProgramSegment(0x1000, b"\x01\x00", 2, "rx"),),
        )
        result = AslMultiPeElfRunner("/unused").run_image(
            image,
            pe_count=1,
            max_instructions=2,
            executor_factory=FakeExecutor,
        )

        self.assertFalse(result.ok)
        self.assertEqual(result.termination, "step_failed")
        self.assertEqual(result.steps[-1].status, "fetch_failed")
        self.assertIn("executable PT_LOAD", result.steps[-1].error)

    def test_control_flow_can_enter_another_executable_segment(self):
        image = ProgramImage(
            entry_point=0x1000,
            segments=(
                ProgramSegment(0x1000, b"\x01\x00", 2, "rx"),
                ProgramSegment(0x2000, b"\x02\x00", 2, "rx"),
            ),
        )
        executor = FakeExecutor(next_pc=0x2000)
        result = AslMultiPeElfRunner("/unused").run_image(
            image,
            pe_count=1,
            max_instructions=2,
            executor_factory=lambda: executor,
            finisher=CallbackFinisher(
                lambda context, _request, _execution: context.instruction_count == 2
            ),
        )

        self.assertTrue(result.ok)
        self.assertEqual([step.address for step in result.steps], [0x1000, 0x2000])

    def test_invalid_context_and_budget_are_rejected(self):
        runner = AslMultiPeElfRunner("/unused")
        with self.assertRaises(ValueError):
            runner.run_image(self.image, pe_count=0)
        with self.assertRaises(ValueError):
            runner.run_image(self.image, max_instructions=0)

    def test_portable_multi_pe_auto_fetch_fails_closed(self):
        runner = AslMultiPeElfRunner("/unused", model_profile="portable")
        with self.assertRaisesRegex(
            UnsupportedPeStateScope, "requires model_profile='linx-runtime'"
        ):
            runner.run_image(self.image, pe_count=2, max_instructions=2)

    def test_single_worker_scope_rejects_multiple_pe_contexts(self):
        executor = AslWorkerExecutor("/unused", worker_scope="single")
        contexts = (PeContext(0, 0, 0x1000), PeContext(1, 1, 0x1000))
        with self.assertRaises(UnsupportedPeStateScope):
            executor.start(self.image, contexts)
        executor.close()

    def test_worker_factory_type_error_is_not_downgraded(self):
        def broken_factory(*_args, **_kwargs):
            raise TypeError("internal memory_read failure")

        executor = AslWorkerExecutor("/unused", worker_factory=broken_factory)
        with self.assertRaisesRegex(TypeError, "internal memory_read failure"):
            executor.start(self.image, (PeContext(0, 0, 0x1000),))

    def test_asl_owned_step_failure_returns_failed_execution(self):
        worker = FailingAutoWorker()
        executor = AslWorkerExecutor(
            "/unused", worker_factory=lambda *_args, **_kwargs: worker
        )
        context = PeContext(0, 0, 0x1000)
        executor.start(self.image, (context,))
        try:
            request, execution = executor.step_next(context)
        finally:
            executor.close()

        self.assertEqual(request.encoding, b"\0\0")
        self.assertFalse(execution.ok)
        self.assertIn("synthetic host failure", execution.error)

    def test_single_worker_scope_selects_context_for_one_pe(self):
        worker = ScopedFakeWorker()
        executor = AslWorkerExecutor(
            "/unused",
            worker_scope="single",
            worker_factory=lambda *_args, **_kwargs: worker,
        )
        context = PeContext(0, 0, 0x1000)
        executor.start(self.image, (context,))
        result = executor.execute(
            context,
            InstructionRequest(pc=0x1000, encoding=b"\x01\x00", pe_id=0, thread_id=0),
        )
        executor.close()
        self.assertTrue(result.ok)
        self.assertEqual(
            [call[0] for call in worker.calls],
            ["start", "ping", "select_pe", "select_pe", "set_tpc", "step", "stop"],
        )

    def test_experimental_core_executes_without_host_semantic_deduplication(self):
        worker = CoreScopedFakeWorker()
        executor = AslWorkerExecutor(
            "/unused",
            worker_scope="core",
            experimental_core=True,
            worker_factory=lambda *_args, **_kwargs: worker,
        )
        contexts = (PeContext(0, 0, 0x1000), PeContext(1, 1, 0x1000))
        executor.start(self.image, contexts)
        try:
            executor.begin_round()
            first = executor.execute(
                contexts[0],
                InstructionRequest(pc=0x1000, encoding=b"\x01\x00", pe_id=0, thread_id=0),
            )
            second = executor.execute(
                contexts[1],
                InstructionRequest(pc=0x1000, encoding=b"\x01\x00", pe_id=1, thread_id=1),
            )
            self.assertTrue(first.ok)
            self.assertTrue(second.ok)
            self.assertEqual(worker.step_count, 2)
            self.assertEqual(first.next_pc, second.next_pc)
        finally:
            executor.close()

    def test_core_scope_requires_explicit_experimental_opt_in(self):
        with self.assertRaisesRegex(UnsupportedPeStateScope, "experimental"):
            AslWorkerExecutor("/unused", worker_scope="core")
        with self.assertRaisesRegex(UnsupportedPeStateScope, "experimental"):
            AslMultiPeElfRunner("/unused", worker_scope="core")

    def test_per_pe_stack_banks_initialize_profile_selected_sp(self):
        workers = []

        def make_worker(*_args, **_kwargs):
            worker = ScopedFakeWorker()
            workers.append(worker)
            return worker

        executor = AslWorkerExecutor(
            "/unused",
            worker_factory=make_worker,
            stack_pointer=0x8000,
            stack_size=0x1000,
            model_profile="portable",
        )
        contexts = (PeContext(0, 0, 0x1000), PeContext(1, 1, 0x1000))
        executor.start(self.image, contexts)
        try:
            frame_sp = AslModelProfile.select("portable").frame_sp_index
            self.assertIn(f"WriteGPR({frame_sp},", workers[0].calls[0][1])
            self.assertIn("0x7ff0", workers[0].calls[0][1])
            self.assertIn("0x8ff0", workers[1].calls[0][1])
            self.assertEqual(executor.memory_bridge.stack_pointer_for(0), 0x7ff0)
            self.assertEqual(executor.memory_bridge.stack_pointer_for(1), 0x8ff0)
        finally:
            executor.close()


if __name__ == "__main__":
    unittest.main()
