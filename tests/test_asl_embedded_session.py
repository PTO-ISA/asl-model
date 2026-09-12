import tempfile
import unittest
import os
from pathlib import Path
from unittest import mock

from asl_model.session import (
    EmbeddedAslSession,
    SessionClosedError,
    SessionError,
)
from asl_model.embedded import EmbeddedAslWorker
from asl_model.runtime.asl_elf import AslElfRunner
from asl_model.runtime.multi_elf import AslMultiPeElfRunner
from tools.generate_smoke_elf import build as build_smoke_elf


try:
    EmbeddedAslWorker._find_tool("ocamlopt")
    HAS_OCAMLOPT = True
except Exception:
    HAS_OCAMLOPT = False


class FakeWorker:
    def __init__(self, *_args, **_kwargs):
        self.calls = []
        self.step_statuses = []
        self.started = False
        self.stopped = False

    def start(self, initial_source=""):
        self.calls.append(("start", initial_source))
        self.started = True

    def ping(self):
        self.calls.append(("ping",))

    def step(self, instruction, length_bits):
        self.calls.append(("step", instruction, length_bits))
        return self.step_statuses.pop(0) if self.step_statuses else 0

    def reset(self):
        self.calls.append(("reset",))

    def stop(self):
        self.calls.append(("stop",))
        self.stopped = True


class EmbeddedAslSessionUnitTest(unittest.TestCase):
    def setUp(self):
        self.artifact = mock.patch(
            "asl_model.session.AslArtifact.load",
            return_value=mock.Mock(as_dict=lambda: {"spec": "test"}),
        )
        self.artifact.start()
        self.worker = mock.patch("asl_model.session.EmbeddedAslWorker", FakeWorker)
        self.worker.start()
        self.addCleanup(self.worker.stop)
        self.addCleanup(self.artifact.stop)

    def test_worker_is_started_once_and_stateful_instruction_api_is_used(self):
        session = EmbeddedAslSession.create(Path("/tmp/pto-spec"), initial_source="Init();")
        self.addCleanup(session.destroy)
        worker = session._worker

        first = session.step_instruction(0x1234, 32, label="first")
        snapshot = session.snapshot()
        second = session.step("ExecutePTOInstruction(0x1235, 32);")

        self.assertTrue(first.ok)
        self.assertTrue(second.ok)
        self.assertEqual(snapshot.step_count, 1)
        self.assertEqual([call[0] for call in worker.calls], ["start", "ping", "step", "step"])
        self.assertEqual(worker.calls[2][1:], (0x1234, 32))
        self.assertEqual(worker.calls[3][1:], (0x1235, 32))

    def test_reset_replays_prefix_without_restarting_worker(self):
        session = EmbeddedAslSession.create(Path("/tmp/pto-spec"))
        self.addCleanup(session.destroy)
        worker = session._worker
        session.step_instruction(1, 16)
        checkpoint = session.snapshot()
        session.step_instruction(2, 16)
        session.reset(checkpoint)

        self.assertEqual(session.snapshot().step_count, 1)
        self.assertEqual([call[0] for call in worker.calls], [
            "start", "ping", "step", "step", "reset", "step"
        ])

    def test_reset_without_snapshot_returns_to_initial_state(self):
        session = EmbeddedAslSession.create(Path("/tmp/pto-spec"))
        self.addCleanup(session.destroy)
        worker = session._worker
        session.step_instruction(1, 16)
        session.step_instruction(2, 16)

        session.reset()

        self.assertEqual(session.snapshot().step_count, 0)
        self.assertEqual(
            [call[0] for call in worker.calls],
            ["start", "ping", "step", "step", "reset"],
        )

    def test_source_step_has_an_explicit_instruction_only_boundary(self):
        session = EmbeddedAslSession.create(Path("/tmp/pto-spec"))
        self.addCleanup(session.destroy)
        with self.assertRaises(SessionError):
            session.step("WriteTPC(100);")
        with self.assertRaises(ValueError):
            session.step_instruction(1, 24)

    def test_rejected_step_restores_last_committed_prefix(self):
        session = EmbeddedAslSession.create(Path("/tmp/pto-spec"))
        self.addCleanup(session.destroy)
        worker = session._worker
        session.step_instruction(1, 16)
        worker.step_statuses.extend((1, 0))

        rejected = session.step_instruction(2, 16)

        self.assertEqual(rejected.status, "rejected")
        self.assertEqual(session.snapshot().step_count, 1)
        self.assertEqual(
            [call[0] for call in worker.calls],
            ["start", "ping", "step", "step", "reset", "step"],
        )

    def test_failed_snapshot_replay_closes_session(self):
        session = EmbeddedAslSession.create(Path("/tmp/pto-spec"))
        self.addCleanup(session.destroy)
        worker = session._worker
        session.step_instruction(1, 16)
        checkpoint = session.snapshot()
        session.step_instruction(2, 16)
        worker.step_statuses.append(1)

        with self.assertRaises(SessionError):
            session.reset(checkpoint)

        self.assertTrue(worker.stopped)
        with self.assertRaises(SessionClosedError):
            session.snapshot()

    def test_snapshot_validation_and_destroy(self):
        first = EmbeddedAslSession.create(Path("/tmp/pto-spec"))
        second = EmbeddedAslSession.create(Path("/tmp/pto-spec"))
        self.addCleanup(first.destroy)
        self.addCleanup(second.destroy)
        first.step_instruction(1, 16)
        snapshot = first.snapshot()
        with self.assertRaises(SessionError):
            second.reset(snapshot)
        first.destroy()
        with self.assertRaises(SessionClosedError):
            first.snapshot()

    def test_pe_selection_protocol_is_explicit(self):
        worker = EmbeddedAslWorker.__new__(EmbeddedAslWorker)
        worker.request = mock.Mock(side_effect=["status 0", "value 17", "value 2"])
        worker.select_pe(2)
        self.assertEqual(worker.peek_pe_gpr(2, 17), 17)
        self.assertEqual(worker.peek_selected_pe(), 2)
        self.assertEqual(
            worker.request.call_args_list,
            [mock.call("select_pe 2"), mock.call("peek_pe_gpr 2 17"), mock.call("peek_pe")],
        )

    def test_asl_owned_step_result_is_parsed_without_host_decode(self):
        worker = EmbeddedAslWorker.__new__(EmbeddedAslWorker)
        worker.request = mock.Mock(return_value="step_result 0 32 0 4100 5")

        result = worker.step_auto()

        self.assertEqual(result.status, 0)
        self.assertEqual(result.length_bits, 32)
        self.assertEqual(result.tpc, 4100)
        self.assertEqual(result.instruction, 5)
        worker.request.assert_called_once_with("step_auto")

    def test_worker_binds_host_access_permission_hooks(self):
        source = (
            Path(__file__).parents[1]
            / "src" / "asl_model" / "embedded" / "Worker.ml"
        ).read_text(encoding="utf-8")
        self.assertIn('"HostInstructionAccessPermitted"', source)
        self.assertIn('"HostDataAccessPermitted"', source)
        self.assertGreaterEqual(source.count("AST.L_Bool true"), 2)
        self.assertIn("DeterminePTOInstructionLength(instruction[15:0])", source)
        self.assertNotIn("InferInstructionLength", source)
        self.assertIn("max_cached_bytes = 262144", source)
        self.assertIn("not (Hashtbl.mem bytes address)", source)
        self.assertIn("Hashtbl.length bytes >= max_cached_bytes", source)

    def test_worker_build_cache_uses_a_cross_process_lock_and_revalidates(self):
        source = (
            Path(__file__).parents[1] / "src" / "asl_model" / "embedded" / "worker.py"
        ).read_text(encoding="utf-8")
        self.assertIn('lock_path = target / "build.lock"', source)
        self.assertIn("fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)", source)
        self.assertGreaterEqual(source.count("self._cached_identity("), 3)


class EmbeddedAslRefIntegrationTest(unittest.TestCase):
    """One opt-in integration test for the compiled persistent worker."""

    SPEC_ROOT = Path(os.environ.get("PTO_SPEC_ROOT", "/nonexistent/pto-spec"))
    ASLREF_ROOT = Path(
        os.environ.get("PTO_ASLREF_ROOT", SPEC_ROOT / ".cache" / "herdtools7")
    )
    ASLREF_BUILD = ASLREF_ROOT / "_build" / "default" / "asllib" / "asllib.cmxa"

    INTEGRATION_READY = (
        ASLREF_BUILD.is_file()
        and HAS_OCAMLOPT
        and (SPEC_ROOT / "build" / "pto-spec.asl").is_file()
    )

    @unittest.skipUnless(INTEGRATION_READY, "prepared ASLRef native toolchain is unavailable")
    def test_process_pid_and_asl_state_survive_multiple_steps(self):
        from asl_model.embedded import EmbeddedAslWorker

        with tempfile.TemporaryDirectory(prefix="asl-model-worker-test-") as directory:
            worker = EmbeddedAslWorker(self.SPEC_ROOT, cache_root=Path(directory))
            try:
                worker.start(
                    "WriteGPR(1, Zeros{PTO_XLEN} + 7); "
                    "WriteGPR(2, Zeros{PTO_XLEN} + 9);"
                )
                process = worker._process
                self.assertIsNotNone(process)
                pid = process.pid
                add = 0x5 | (3 << 7) | (1 << 15) | (2 << 20) | (3 << 25)
                self.assertEqual(worker.step(add, 32), 0)
                self.assertEqual(worker.request("peek_gpr 3"), "value 16")
                self.assertEqual(worker.request("peek_tpc"), "value 4")
                self.assertEqual(worker._process.pid, pid)
                worker.reset()
                self.assertEqual(worker.request("peek_gpr 1"), "value 7")
                self.assertEqual(worker.request("peek_gpr 3"), "value 0")
            finally:
                worker.stop()

    @unittest.skipUnless(INTEGRATION_READY, "prepared ASLRef native toolchain is unavailable")
    def test_linx_runtime_first_fetch_passes_host_access_preflight(self):
        with tempfile.TemporaryDirectory(prefix="asl-model-linx-smoke-") as directory:
            fixture = Path(directory) / "scalar-add-smoke.elf"
            build_smoke_elf(self.SPEC_ROOT, fixture)
            result = AslElfRunner(
                self.SPEC_ROOT, cache_root=Path(directory), timeout_s=120.0
            ).run(
                fixture,
                max_instructions=1,
                model_profile="linx-runtime",
                expected_machine=0xE9,
            )

        self.assertEqual(len(result.steps), 1)
        self.assertEqual(result.steps[0].status, "committed")
        self.assertNotEqual(result.steps[0].fault_code, 4)

    @unittest.skipUnless(INTEGRATION_READY, "prepared ASLRef native toolchain is unavailable")
    def test_rejected_instruction_restores_live_session_state(self):
        with tempfile.TemporaryDirectory(prefix="asl-model-session-reject-") as directory:
            session = EmbeddedAslSession.create(
                self.SPEC_ROOT, cache_root=Path(directory)
            )
            try:
                before = session.snapshot()
                rejected = session.step_instruction((1 << 64) - 1, 64)
                after = session.snapshot()
                accepted = session.step_instruction(0x5, 32)
            finally:
                session.destroy()

        self.assertEqual(rejected.status, "rejected")
        self.assertEqual(after, before)
        self.assertEqual(accepted.status, "committed")

    @unittest.skipUnless(INTEGRATION_READY, "prepared ASLRef native toolchain is unavailable")
    def test_linx_runtime_two_pe_uses_asl_owned_fetch(self):
        with tempfile.TemporaryDirectory(prefix="asl-model-linx-pe2-") as directory:
            fixture = Path(directory) / "scalar-add-smoke.elf"
            build_smoke_elf(self.SPEC_ROOT, fixture)
            result = AslMultiPeElfRunner(
                self.SPEC_ROOT,
                model_profile="linx-runtime",
                worker_scope="per-pe",
                cache_root=Path(directory),
            ).run(fixture, pe_count=2, max_instructions=2)

        self.assertTrue(result.ok)
        self.assertEqual([step.pe_id for step in result.steps], [0, 1])
        self.assertTrue(all(step.length_bits == 32 for step in result.steps))
        self.assertTrue(all(step.status == "committed" for step in result.steps))

    @unittest.skipUnless(INTEGRATION_READY, "prepared ASLRef native toolchain is unavailable")
    def test_portable_width_rule_is_total_before_illegal_decode(self):
        with tempfile.TemporaryDirectory(prefix="asl-model-width-rule-") as directory:
            worker = EmbeddedAslWorker(self.SPEC_ROOT, cache_root=Path(directory))
            try:
                worker.start()
                encoding = (1 << 64) - 1
                width = worker.decode_length(encoding)
                status = worker.step(encoding, width)
                fault = worker.peek_fault()
            finally:
                worker.stop()

        self.assertIn(width, {16, 32, 48, 64})
        self.assertNotEqual(status, 0)
        self.assertEqual(fault, 2)


if __name__ == "__main__":
    unittest.main()
