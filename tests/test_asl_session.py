import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from asl_model.session import (
    ProcessAslSession,
    SessionClosedError,
    SessionError,
)


class ProcessAslSessionTest(unittest.TestCase):
    def make_session(self):
        directory = tempfile.TemporaryDirectory()
        root = Path(directory.name)
        (root / "build").mkdir()
        (root / "scripts").mkdir()
        (root / "build" / "pto-spec.asl").write_text("// spec\n", encoding="utf-8")
        (root / "build" / "decoders.asl").write_text("// decoder\n", encoding="utf-8")
        (root / "build" / "asl-source-order.txt").write_text("source\n", encoding="utf-8")
        (root / "scripts" / "aslref").write_text("#!/bin/sh\n", encoding="utf-8")
        artifact = mock.patch(
            "asl_model.session.AslArtifact.load",
            return_value=mock.Mock(as_dict=lambda: {"spec": "test"}),
        )
        artifact.start()
        self.addCleanup(artifact.stop)
        self.addCleanup(directory.cleanup)
        session = ProcessAslSession.create(root, initial_source="ResetProfileState();")
        self.addCleanup(session.destroy)
        return session

    def test_create_step_snapshot_reset_and_destroy(self):
        session = self.make_session()
        programs = []

        def run(program):
            programs.append(program)
            return subprocess.CompletedProcess(["aslref"], 0, "", "")

        with mock.patch.object(session, "_run", side_effect=run):
            first = session.step("WriteTPC(Zeros{PTO_XLEN} + 100);", label="write")
            snapshot = session.snapshot()
            second = session.step("WriteTPC(Zeros{PTO_XLEN} + 101);", label="update")
            self.assertEqual(first.status, "committed")
            self.assertEqual(first.step_index, 1)
            self.assertEqual(second.step_index, 2)
            self.assertEqual(snapshot.step_count, 1)
            session.reset(snapshot)
            self.assertEqual(session.snapshot().step_count, 1)
            session.reset()
            self.assertEqual(session.snapshot().step_count, 0)
            session.step("// after reset")
            self.assertIn("ResetProfileState();", programs[-1])
            self.assertNotIn("WriteTPC", programs[-1])

        session.destroy()
        with self.assertRaises(SessionClosedError):
            session.snapshot()

    def test_failed_step_is_transactional(self):
        session = self.make_session()
        programs = []

        def run(program):
            programs.append(program)
            code = 1 if "reject-me" in program else 0
            return subprocess.CompletedProcess(["aslref"], code, "", "failure" if code else "")

        with mock.patch.object(session, "_run", side_effect=run):
            rejected = session.step("// reject-me")
            self.assertEqual(rejected.status, "rejected")
            self.assertEqual(rejected.step_index, 0)
            accepted = session.step("// accepted")
            self.assertEqual(accepted.status, "committed")
            self.assertEqual(accepted.step_index, 1)
            self.assertNotIn("reject-me", programs[-1])

    def test_snapshot_cannot_cross_sessions(self):
        first = self.make_session()
        second = self.make_session()
        with mock.patch.object(first, "_run", return_value=subprocess.CompletedProcess([], 0, "", "")):
            first.step("// first")
        snapshot = first.snapshot()
        with self.assertRaises(SessionError):
            second.reset(snapshot)

    def test_invalid_step_and_timeout_are_explicit(self):
        session = self.make_session()
        with self.assertRaises(ValueError):
            session.step("  ")
        with self.assertRaises(ValueError):
            ProcessAslSession.create(session.pto_spec_root, timeout_s=0)


if __name__ == "__main__":
    unittest.main()
