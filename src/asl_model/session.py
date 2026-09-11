"""Stateful ASL execution sessions.

The public session contract is deliberately independent from ASLRef's command
line interface.  The implementation currently uses deterministic replay: each
step is appended to a small ASL program and the pinned ASLRef process executes
the complete program.  This is slower than an embedded interpreter, but it
gives us the right state, reset, and snapshot semantics while keeping the
future VM boundary stable.
"""

from __future__ import annotations

import hashlib
import subprocess
import tempfile
import threading
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from .artifact import AslArtifact
from .embedded import EmbeddedAslWorker, EmbeddedWorkerError
from .paths import runtime_environment


class SessionError(RuntimeError):
    """Base error for invalid session operations."""


class SessionClosedError(SessionError):
    """Raised when an operation is attempted after :meth:`destroy`."""


@dataclass(frozen=True)
class SessionSnapshot:
    """Opaque, immutable point-in-time handle for one session.

    A snapshot stores the replay prefix rather than a serialized ASL VM state.
    The session id and history digest prevent accidentally restoring a handle
    belonging to another session or artifact.
    """

    session_id: str
    step_count: int
    history_sha256: str

    def as_dict(self) -> dict[str, object]:
        return {
            "session_id": self.session_id,
            "step_count": self.step_count,
            "history_sha256": self.history_sha256,
        }


@dataclass(frozen=True)
class SessionStepResult:
    """Result of one ASL session step."""

    session_id: str
    step_index: int
    status: str
    returncode: int
    stdout: str
    stderr: str
    artifact: dict[str, str]

    @property
    def ok(self) -> bool:
        return self.returncode == 0

    def as_dict(self) -> dict[str, object]:
        return {
            "session_id": self.session_id,
            "step_index": self.step_index,
            "status": self.status,
            "returncode": self.returncode,
            "stdout": self.stdout,
            "stderr": self.stderr,
            "artifact": self.artifact,
        }


class AslSession:
    """Backend-neutral session contract.

    Implementations must treat ``step`` as a state transition and must make a
    failed transition observable in its result.  ``snapshot`` returns a
    restore handle; ``reset(snapshot)`` restores that handle, while
    ``reset()`` returns to the session's initial state.
    """

    @classmethod
    def create(cls, pto_spec_root: Path, initial_source: str = "") -> "AslSession":
        raise NotImplementedError

    def reset(self, snapshot: Optional[SessionSnapshot] = None) -> None:
        raise NotImplementedError

    def step(self, source: str, *, label: str = "") -> SessionStepResult:
        raise NotImplementedError

    def step_instruction(
        self, instruction: int, length_bits: int, *, label: str = ""
    ) -> SessionStepResult:
        """Execute one encoded PTO instruction.

        Source-oriented sessions may leave this operation unsupported.  The
        embedded ASL session implements it as its native, zero-reparse fast
        path; keeping it on the common contract lets a runtime adapter select
        the operation without knowing the concrete backend.
        """

        raise NotImplementedError

    def snapshot(self) -> SessionSnapshot:
        raise NotImplementedError

    def destroy(self) -> None:
        raise NotImplementedError


class ProcessAslSession(AslSession):
    """ASLRef-backed session with deterministic process-level replay.

    ASLRef currently exposes a command-line program runner rather than a
    stable embeddable VM ABI.  This class therefore keeps the session alive in
    Python and reruns the complete ASL prefix for each successful step.  The
    architectural state is still created, read, and modified exclusively by
    ASL.  A future ``EmbeddedAslSession`` can implement the same contract and
    replace this class without changing callers.
    """

    backend_name = "asl-process"

    @classmethod
    def create(
        cls,
        pto_spec_root: Path,
        initial_source: str = "",
        *,
        timeout_s: float = 120.0,
    ) -> "ProcessAslSession":
        return cls(pto_spec_root, initial_source=initial_source, timeout_s=timeout_s)

    def __init__(
        self,
        pto_spec_root: Path,
        *,
        initial_source: str = "",
        timeout_s: float = 120.0,
    ):
        self.pto_spec_root = Path(pto_spec_root).resolve()
        self.aslref = self.pto_spec_root / "scripts" / "aslref"
        if not self.aslref.is_file():
            raise FileNotFoundError(f"missing ASLRef launcher: {self.aslref}")
        if timeout_s <= 0:
            raise ValueError("ASL session timeout_s must be positive")
        self.artifact = AslArtifact.load(self.pto_spec_root)
        self._spec = (self.pto_spec_root / "build" / "pto-spec.asl").read_text(
            encoding="utf-8"
        )
        self._session_id = uuid.uuid4().hex
        self._initial_source = initial_source
        self.timeout_s = timeout_s
        self._operations: list[str] = []
        self._labels: list[str] = []
        self._closed = False
        self._lock = threading.RLock()
        self._workdir = Path(tempfile.mkdtemp(prefix="pto-asl-session-"))

    def reset(self, snapshot: Optional[SessionSnapshot] = None) -> None:
        with self._lock:
            self._check_open()
            if snapshot is None:
                self._operations.clear()
                self._labels.clear()
                return
            self._validate_snapshot(snapshot)
            del self._operations[snapshot.step_count :]
            del self._labels[snapshot.step_count :]

    def step(self, source: str, *, label: str = "") -> SessionStepResult:
        with self._lock:
            self._check_open()
            if not isinstance(source, str) or not source.strip():
                raise ValueError("ASL session step must be a non-empty source string")

            candidate = self._operations + [source]
            completed = self._run(self._program(candidate))
            committed = completed.returncode == 0
            if committed:
                self._operations.append(source)
                self._labels.append(label)
            return SessionStepResult(
                session_id=self._session_id,
                step_index=len(self._operations),
                status="committed" if committed else "rejected",
                returncode=completed.returncode,
                stdout=completed.stdout,
                stderr=completed.stderr,
                artifact=self.artifact.as_dict(),
            )

    def step_instruction(
        self, instruction: int, length_bits: int, *, label: str = ""
    ) -> SessionStepResult:
        """Execute an encoded instruction through the source replay path.

        This compatibility implementation deliberately remains slow.  The
        embedded implementation below is the path that keeps the ASL VM alive
        and avoids reparsing/retyping each instruction.
        """

        if not 0 <= instruction < (1 << 64):
            raise ValueError("instruction must fit in 64 bits")
        if length_bits not in {16, 32, 48, 64}:
            raise ValueError("length_bits must be one of 16, 32, 48, or 64")
        source = (
            "let _asl_model_status = ExecutePTOInstruction("
            f"Zeros{{64}} + 0x{instruction:x}, {length_bits});"
        )
        return self.step(source, label=label)

    def snapshot(self) -> SessionSnapshot:
        with self._lock:
            self._check_open()
            return SessionSnapshot(
                session_id=self._session_id,
                step_count=len(self._operations),
                history_sha256=self._history_sha256(self._operations),
            )

    def destroy(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
            # Keep cleanup deliberately narrow and recoverable: this directory
            # is owned by this session and contains only generated ASL input.
            for path in self._workdir.iterdir():
                if path.is_file() or path.is_symlink():
                    path.unlink()
            self._workdir.rmdir()

    def _program(self, operations: list[str]) -> str:
        body = []
        if self._initial_source.strip():
            body.append(self._initial_source.strip())
        body.extend(operation.strip() for operation in operations)
        body_text = "\n\n".join(body)
        return (
            self._spec
            + "\n\n// Generated by ProcessAslSession.\n"
            + "func main() => integer\n"
            + "begin\n"
            + body_text
            + "\n    return 0;\n"
            + "end;\n"
        )

    def _run(self, program: str) -> subprocess.CompletedProcess[str]:
        input_file = self._workdir / "session.asl"
        input_file.write_text(program, encoding="utf-8")
        try:
            return subprocess.run(
                [str(self.aslref), "--type-check-no-warn", str(input_file)],
                cwd=self.pto_spec_root,
                env=self._environment(),
                capture_output=True,
                text=True,
                timeout=self.timeout_s,
            )
        except subprocess.TimeoutExpired as error:
            output = error.stdout or ""
            stderr = error.stderr or ""
            return subprocess.CompletedProcess(
                args=error.cmd,
                returncode=124,
                stdout=output if isinstance(output, str) else output.decode(errors="replace"),
                stderr=(stderr if isinstance(stderr, str) else stderr.decode(errors="replace"))
                + f"\nASLRef timed out after {self.timeout_s:g}s",
            )

    def _validate_snapshot(self, snapshot: SessionSnapshot) -> None:
        if snapshot.session_id != self._session_id:
            raise SessionError("snapshot belongs to a different ASL session")
        if snapshot.step_count < 0 or snapshot.step_count > len(self._operations):
            raise SessionError("snapshot is not a valid state in this session")
        actual = self._history_sha256(self._operations[: snapshot.step_count])
        if actual != snapshot.history_sha256:
            raise SessionError("snapshot history does not match this session")

    @staticmethod
    def _history_sha256(operations: list[str]) -> str:
        digest = hashlib.sha256()
        for operation in operations:
            encoded = operation.encode("utf-8")
            digest.update(len(encoded).to_bytes(8, "big"))
            digest.update(encoded)
        return digest.hexdigest()

    def _check_open(self) -> None:
        if self._closed:
            raise SessionClosedError("ASL session has been destroyed")

    @staticmethod
    def _environment() -> dict[str, str]:
        return runtime_environment()


class EmbeddedAslSession(AslSession):
    """Long-lived session backed by an embedded ASLRef interpreter.

    The OCaml worker parses and type-checks the generated PTO ASL exactly once
    when it starts.  ``step_instruction`` then sends encoded instructions over
    a persistent pipe; ASL owns all architectural state and applies each
    transition in order.  Snapshots are replay checkpoints: restoring one
    resets the ASL VM and reissues the recorded prefix without another parse or
    type-check.

    ``step(source)`` remains part of :class:`AslSession`, but arbitrary ASL
    statement injection is intentionally unsupported by an already typed ASL
    program.  It accepts the explicit source form
    ``ExecutePTOInstruction(0x..., 32);`` (or ``instruction 0x... 32``) and
    routes it to ``step_instruction``.  This makes the limitation explicit and
    prevents silently falling back to the replay implementation.
    """

    backend_name = "asl-embedded"

    @classmethod
    def create(
        cls,
        pto_spec_root: Path,
        initial_source: str = "",
        *,
        timeout_s: float = 120.0,
        cache_root: Path | None = None,
    ) -> "EmbeddedAslSession":
        return cls(
            pto_spec_root,
            initial_source=initial_source,
            timeout_s=timeout_s,
            cache_root=cache_root,
        )

    def __init__(
        self,
        pto_spec_root: Path,
        *,
        initial_source: str = "",
        timeout_s: float = 120.0,
        cache_root: Path | None = None,
    ):
        self.pto_spec_root = Path(pto_spec_root).resolve()
        self.artifact = AslArtifact.load(self.pto_spec_root)
        self._session_id = uuid.uuid4().hex
        self._initial_source = initial_source
        self._worker = EmbeddedAslWorker(
            self.pto_spec_root, timeout_s=timeout_s, cache_root=cache_root
        )
        self._history: list[tuple[int, int, str]] = []
        self._closed = False
        self._lock = threading.RLock()
        try:
            self._worker.start(initial_source)
            self._worker.ping()
        except Exception:
            self._worker.stop()
            raise

    def reset(self, snapshot: Optional[SessionSnapshot] = None) -> None:
        with self._lock:
            self._check_open()
            if snapshot is not None:
                self._validate_snapshot(snapshot)
            target = 0 if snapshot is None else snapshot.step_count
            prefix = self._history[:target]
            try:
                self._restore_prefix(prefix)
            except (EmbeddedWorkerError, SessionError):
                self._closed = True
                self._worker.stop()
                raise
            self._history = list(prefix)

    def step(self, source: str, *, label: str = "") -> SessionStepResult:
        instruction, length_bits = _parse_instruction_source(source)
        return self.step_instruction(instruction, length_bits, label=label)

    def step_instruction(
        self, instruction: int, length_bits: int, *, label: str = ""
    ) -> SessionStepResult:
        with self._lock:
            self._check_open()
            if not 0 <= instruction < (1 << 64):
                raise ValueError("instruction must fit in 64 bits")
            if length_bits not in {16, 32, 48, 64}:
                raise ValueError("length_bits must be one of 16, 32, 48, or 64")
            try:
                status_code = self._worker.step(instruction, length_bits)
            except EmbeddedWorkerError as error:
                self._closed = True
                self._worker.stop()
                return SessionStepResult(
                    session_id=self._session_id,
                    step_index=len(self._history),
                    status="failed",
                    returncode=2,
                    stdout="",
                    stderr=str(error),
                    artifact=self.artifact.as_dict(),
                )
            committed = status_code == 0
            if committed:
                self._history.append((instruction, length_bits, label))
            else:
                try:
                    self._restore_prefix(self._history)
                except (EmbeddedWorkerError, SessionError) as error:
                    self._closed = True
                    self._worker.stop()
                    return SessionStepResult(
                        session_id=self._session_id,
                        step_index=len(self._history),
                        status="failed",
                        returncode=2,
                        stdout=f"status {status_code}\n",
                        stderr=f"failed to restore committed prefix: {error}",
                        artifact=self.artifact.as_dict(),
                    )
            return SessionStepResult(
                session_id=self._session_id,
                step_index=len(self._history),
                status="committed" if committed else "rejected",
                returncode=0 if committed else 1,
                stdout=f"status {status_code}\n",
                stderr="",
                artifact=self.artifact.as_dict(),
            )

    def _restore_prefix(self, prefix: list[tuple[int, int, str]]) -> None:
        self._worker.reset()
        for index, (instruction, length_bits, _label) in enumerate(prefix, 1):
            status = self._worker.step(instruction, length_bits)
            if status != 0:
                raise SessionError(
                    "ASL instruction prefix could not be replayed during reset: "
                    f"status {status} at step {index}"
                )

    def snapshot(self) -> SessionSnapshot:
        with self._lock:
            self._check_open()
            return SessionSnapshot(
                session_id=self._session_id,
                step_count=len(self._history),
                history_sha256=self._instruction_history_sha256(self._history),
            )

    def destroy(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
            self._worker.stop()

    def _validate_snapshot(self, snapshot: SessionSnapshot) -> None:
        if snapshot.session_id != self._session_id:
            raise SessionError("snapshot belongs to a different ASL session")
        if snapshot.step_count < 0 or snapshot.step_count > len(self._history):
            raise SessionError("snapshot is not a valid state in this session")
        actual = self._instruction_history_sha256(self._history[: snapshot.step_count])
        if actual != snapshot.history_sha256:
            raise SessionError("snapshot history does not match this session")

    @staticmethod
    def _instruction_history_sha256(
        history: list[tuple[int, int, str]]
    ) -> str:
        digest = hashlib.sha256()
        for instruction, length_bits, _label in history:
            for value in (instruction, length_bits):
                encoded = str(value).encode("ascii")
                digest.update(len(encoded).to_bytes(8, "big"))
                digest.update(encoded)
        return digest.hexdigest()

    def _check_open(self) -> None:
        if self._closed:
            raise SessionClosedError("ASL session has been destroyed")


def _parse_instruction_source(source: str) -> tuple[int, int]:
    """Parse the deliberately narrow source form accepted by embedded ASL."""

    if not isinstance(source, str) or not source.strip():
        raise ValueError("ASL session step must be a non-empty source string")
    import re

    match = re.fullmatch(
        r"\s*(?:(?:instruction\s+)|(?:ExecutePTOInstruction\s*\(\s*))"
        r"(0[xX][0-9a-fA-F]+|[0-9]+)\s*(?:,|\s+)\s*"
        r"(16|32|48|64)\s*\)?\s*;?\s*",
        source,
    )
    if match is None:
        raise SessionError(
            "embedded ASL session accepts encoded instruction source only; "
            "use step_instruction(instruction, length_bits)"
        )
    return int(match.group(1), 0), int(match.group(2), 10)
