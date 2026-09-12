from __future__ import annotations

import fcntl
import hashlib
import json
import os
import selectors
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from ..paths import cache_root as resolve_cache_root, runtime_environment


WORKER_BUILD_SCHEMA = "pto-asl-embedded-worker-build-v3"


class EmbeddedWorkerError(RuntimeError):
    """The embedded ASLRef worker could not be built or contacted."""


@dataclass(frozen=True)
class WorkerIdentity:
    cache_key: str
    aslref_commit: str
    asllib_sha256: str
    worker_sha256: str
    spec_sha256: str
    wrapper_sha256: str

    def as_dict(self) -> dict[str, str]:
        return {
            "cache_key": self.cache_key,
            "aslref_commit": self.aslref_commit,
            "asllib_sha256": self.asllib_sha256,
            "worker_sha256": self.worker_sha256,
            "spec_sha256": self.spec_sha256,
            "wrapper_sha256": self.wrapper_sha256,
        }


@dataclass(frozen=True)
class AutoStepResult:
    """Result of an ASL-owned fetch/decode/execute transition.

    ``instruction`` and ``length_bits`` are reported by the ASL wrapper.  A
    runtime can therefore advance by calling :meth:`step_auto` without
    fetching bytes or duplicating decoder/width rules in Python.
    """

    status: int
    length_bits: int
    fault_code: int
    tpc: int
    instruction: int


class EmbeddedAslWorker:
    """Own one ASLRef interpreter process with a cached native wrapper.

    The wrapper links the pinned ASLRef ``asllib`` library, parses and
    type-checks the generated PTO ASL once, then serves line-oriented commands
    while its ASL global state remains alive.
    """

    def __init__(
        self,
        pto_spec_root: Path,
        *,
        timeout_s: float = 120.0,
        cache_root: Path | None = None,
        memory_read: Callable[[int], int] | None = None,
        memory_read_chunk: Callable[[int, int], bytes] | None = None,
        memory_write: Callable[[int, int], None] | None = None,
    ):
        self.pto_spec_root = Path(pto_spec_root).resolve()
        self.timeout_s = float(timeout_s)
        if self.timeout_s <= 0:
            raise ValueError("embedded ASL worker timeout_s must be positive")
        # Cache is owned by the standalone ASL model installation.  Keeping
        # it under the spec checkout also makes multiple source checkouts
        # independent and avoids assuming a particular superproject layout.
        default_cache = resolve_cache_root() / "embedded_aslref"
        self.cache_root = Path(cache_root or default_cache).resolve()
        self.cache_root.mkdir(parents=True, exist_ok=True)
        self._process: subprocess.Popen[str] | None = None
        self._runtime_dir: Path | None = None
        self._initial_source: str | None = None
        self._stderr_lines: list[str] = []
        self._stderr_thread: threading.Thread | None = None
        self._lock = threading.RLock()
        self.identity: WorkerIdentity | None = None
        self._memory_read = memory_read
        self._memory_read_chunk = memory_read_chunk
        self._memory_write = memory_write
        self._memory_protocol_stats = {
            "chunk_reads": 0,
            "chunk_read_bytes": 0,
            "byte_reads": 0,
            "byte_writes": 0,
        }

    @classmethod
    def build(cls, pto_spec_root: Path, **kwargs: Any) -> "EmbeddedAslWorker":
        worker = cls(pto_spec_root, **kwargs)
        worker.identity, _ = worker._ensure_built()
        return worker

    def start(self, initial_source: str = "", spec_path: Path | None = None) -> None:
        with self._lock:
            if self._process is not None:
                if self._initial_source != initial_source:
                    raise EmbeddedWorkerError(
                        "worker is already running with a different initial source"
                    )
                return
            selected_spec = Path(
                spec_path or self.pto_spec_root / "build" / "pto-spec.asl"
            ).resolve()
            identity, executable = self._ensure_built(selected_spec)
            worker_arguments = [str(executable), str(selected_spec)]
            if initial_source.strip():
                self._runtime_dir = Path(tempfile.mkdtemp(prefix="pto-asl-worker-"))
                initial_path = self._runtime_dir / "initial-state.asl"
                initial_path.write_text(initial_source, encoding="utf-8")
                worker_arguments.append(str(initial_path))
            command = [
                "/bin/sh",
                "-c",
                'stack_limit=$(ulimit -H -s); ulimit -s "$stack_limit"; exec "$@"',
                "pto-asl-worker",
                *worker_arguments,
            ]
            process = subprocess.Popen(
                command,
                cwd=self.pto_spec_root,
                env=self._environment(),
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                bufsize=1,
                start_new_session=True,
            )
            assert process.stdin is not None and process.stdout is not None
            assert process.stderr is not None
            self._process = process
            self._initial_source = initial_source
            self.identity = identity
            self._stderr_lines = []
            self._stderr_thread = threading.Thread(
                target=self._drain_stderr,
                args=(process.stderr,),
                name="pto-aslref-worker-stderr",
                daemon=True,
            )
            self._stderr_thread.start()

    def request(self, command: str) -> str:
        with self._lock:
            self.start(self._initial_source or "")
            process = self._process
            if process is None or process.stdin is None or process.stdout is None:
                raise EmbeddedWorkerError("worker is not running")
            if process.poll() is not None:
                raise EmbeddedWorkerError(self._failure("worker exited"))
            try:
                process.stdin.write(command.rstrip("\n") + "\n")
                process.stdin.flush()
            except (BrokenPipeError, OSError) as error:
                raise EmbeddedWorkerError(self._failure(str(error))) from error
            while True:
                line = self._readline(process.stdout)
                if line is None:
                    raise EmbeddedWorkerError(self._failure("worker closed stdout"))
                if line.startswith("mem_read_chunk "):
                    words = line.split()
                    if len(words) != 3:
                        raise EmbeddedWorkerError(
                            f"invalid ASL memory chunk request: {line}"
                        )
                    try:
                        address, size = int(words[1], 0), int(words[2], 0)
                    except ValueError as error:
                        raise EmbeddedWorkerError(
                            f"invalid ASL memory chunk request: {line}"
                        ) from error
                    if not 0 < size <= 4096:
                        raise EmbeddedWorkerError(
                            f"invalid ASL memory chunk size: {size}"
                        )
                    if self._memory_read_chunk is not None:
                        payload = bytes(self._memory_read_chunk(address, size))
                    elif self._memory_read is not None:
                        payload = bytes(
                            self._memory_read(address + offset)
                            for offset in range(size)
                        )
                    else:
                        raise EmbeddedWorkerError(
                            "ASL requested memory but no host memory bridge is attached"
                        )
                    if not payload or len(payload) > size:
                        raise EmbeddedWorkerError(
                            "host memory bridge returned an invalid chunk"
                        )
                    self._memory_protocol_stats["chunk_reads"] += 1
                    self._memory_protocol_stats["chunk_read_bytes"] += len(payload)
                    process.stdin.write(f"mem_chunk {address} {payload.hex()}\n")
                    process.stdin.flush()
                    continue
                if line.startswith("mem_read "):
                    if self._memory_read is None:
                        raise EmbeddedWorkerError(
                            "ASL requested memory but no host memory bridge is attached"
                        )
                    try:
                        address = int(line.split(" ", 1)[1], 0)
                        value = int(self._memory_read(address))
                    except (ValueError, TypeError, IndexError) as error:
                        raise EmbeddedWorkerError(
                            f"invalid ASL memory read request: {line}"
                        ) from error
                    if not 0 <= value <= 0xFF:
                        raise EmbeddedWorkerError(
                            f"host memory bridge returned non-byte value: {value}"
                        )
                    self._memory_protocol_stats["byte_reads"] += 1
                    process.stdin.write(f"mem_value {value}\n")
                    process.stdin.flush()
                    continue
                if line.startswith("mem_write "):
                    if self._memory_write is None:
                        raise EmbeddedWorkerError(
                            "ASL requested memory write but no host memory bridge is attached"
                        )
                    words = line.split()
                    if len(words) != 3:
                        raise EmbeddedWorkerError(
                            f"invalid ASL memory write request: {line}"
                        )
                    try:
                        address, value = int(words[1], 0), int(words[2], 0)
                        self._memory_write(address, value)
                    except (ValueError, TypeError, IndexError) as error:
                        raise EmbeddedWorkerError(
                            f"invalid ASL memory write request: {line}"
                        ) from error
                    self._memory_protocol_stats["byte_writes"] += 1
                    process.stdin.write("mem_status 0\n")
                    process.stdin.flush()
                    continue
                return line

    def ping(self) -> None:
        if self.request("ping") != "status 0":
            raise EmbeddedWorkerError("unexpected ping response")

    def clear_memory_cache(self) -> None:
        if self.request("clear_mem_cache") != "status 0":
            raise EmbeddedWorkerError("embedded ASL memory cache clear failed")

    def memory_protocol_stats(self) -> dict[str, int]:
        return dict(self._memory_protocol_stats)

    def step(self, instruction: int, length_bits: int) -> int:
        if not 0 <= instruction < (1 << 64):
            raise ValueError("instruction must fit in 64 bits")
        if length_bits not in {16, 32, 48, 64}:
            raise ValueError("length_bits must be one of 16, 32, 48, or 64")
        response = self.request(f"step 0x{instruction:x} {length_bits}")
        prefix, _, status = response.partition(" ")
        if prefix != "status":
            raise EmbeddedWorkerError(f"unexpected step response: {response}")
        try:
            return int(status, 10)
        except ValueError as error:
            raise EmbeddedWorkerError(f"invalid step response: {response}") from error

    def step_auto(self) -> AutoStepResult:
        """Let ASL fetch at TPC, infer width, and execute one instruction.

        The host-side ``mem_read`` requests emitted by ASL are serviced by
        the attached memory bridge.  ``step`` remains available for clients
        that need the legacy explicit-encoding protocol.
        """

        response = self.request("step_auto")
        fields = response.split()
        if len(fields) != 6 or fields[0] != "step_result":
            raise EmbeddedWorkerError(f"unexpected step_auto response: {response}")
        try:
            status, length_bits, fault_code, tpc, instruction = (
                int(value, 10) for value in fields[1:]
            )
        except ValueError as error:
            raise EmbeddedWorkerError(
                f"invalid step_auto response: {response}"
            ) from error
        if status not in {0, 1}:
            raise EmbeddedWorkerError(f"invalid step_auto status: {status}")
        if length_bits not in {0, 16, 32, 48, 64}:
            raise EmbeddedWorkerError(f"invalid step_auto length: {length_bits}")
        if not 0 <= instruction < (1 << 64):
            raise EmbeddedWorkerError(f"invalid step_auto instruction: {instruction}")
        if tpc < 0:
            raise EmbeddedWorkerError(f"invalid step_auto TPC: {tpc}")
        return AutoStepResult(status, length_bits, fault_code, tpc, instruction)

    def decode_length(self, instruction: int) -> int:
        if not 0 <= instruction < (1 << 64):
            raise ValueError("instruction must fit in 64 bits")
        response = self.request(f"decode_length 0x{instruction:x}")
        prefix, _, value = response.partition(" ")
        if prefix != "value":
            raise EmbeddedWorkerError(f"unexpected decode_length response: {response}")
        try:
            length_bits = int(value, 10)
        except ValueError as error:
            raise EmbeddedWorkerError(
                f"invalid decode_length response: {response}"
            ) from error
        if length_bits not in {0, 16, 32, 48, 64}:
            raise EmbeddedWorkerError(
                f"invalid decoded instruction length: {length_bits}"
            )
        return length_bits

    def peek_tpc(self) -> int:
        """Read the ASL-owned TPC after the last transition."""

        response = self.request("peek_tpc")
        prefix, _, value = response.partition(" ")
        if prefix != "value":
            raise EmbeddedWorkerError(f"unexpected peek_tpc response: {response}")
        try:
            return int(value, 10)
        except ValueError as error:
            raise EmbeddedWorkerError(
                f"invalid peek_tpc response: {response}"
            ) from error

    def read_memory_byte(self, address: int) -> int:
        """Read one guest byte through the ASL worker host bridge."""

        if address < 0:
            raise ValueError("memory address must be non-negative")
        response = self.request(f"read_mem 0x{address:x}")
        prefix, _, value = response.partition(" ")
        if prefix != "value":
            raise EmbeddedWorkerError(f"unexpected read_mem response: {response}")
        try:
            byte = int(value, 10)
        except ValueError as error:
            raise EmbeddedWorkerError(
                f"invalid read_mem response: {response}"
            ) from error
        if not 0 <= byte <= 0xFF:
            raise EmbeddedWorkerError(f"read_mem returned non-byte value: {byte}")
        return byte

    def write_memory_byte(self, address: int, value: int) -> None:
        """Write one guest byte through the ASL worker host bridge."""

        if address < 0:
            raise ValueError("memory address must be non-negative")
        if not 0 <= value <= 0xFF:
            raise ValueError("memory value must fit in one byte")
        response = self.request(f"write_mem 0x{address:x} {value}")
        if response != "status 0":
            raise EmbeddedWorkerError(f"unexpected write_mem response: {response}")

    def peek_fault(self) -> int:
        response = self.request("peek_fault")
        prefix, _, value = response.partition(" ")
        if prefix != "value":
            raise EmbeddedWorkerError(f"unexpected peek_fault response: {response}")
        try:
            return int(value, 10)
        except ValueError as error:
            raise EmbeddedWorkerError(
                f"invalid peek_fault response: {response}"
            ) from error

    def select_pe(self, pe_id: int) -> None:
        """Select the ASL memory-agent/PE context used by scalar accessors."""

        if not isinstance(pe_id, int) or pe_id < 0:
            raise ValueError("PE id must be a non-negative integer")
        response = self.request(f"select_pe {pe_id}")
        if response != "status 0":
            raise EmbeddedWorkerError(f"unexpected select_pe response: {response}")

    def peek_pe_gpr(self, pe_id: int, index: int) -> int:
        """Read one GPR from an explicit ASL PE register file."""

        if not isinstance(pe_id, int) or pe_id < 0:
            raise ValueError("PE id must be a non-negative integer")
        if not isinstance(index, int) or index < 0:
            raise ValueError("GPR index must be a non-negative integer")
        response = self.request(f"peek_pe_gpr {pe_id} {index}")
        prefix, _, value = response.partition(" ")
        if prefix != "value":
            raise EmbeddedWorkerError(f"unexpected peek_pe_gpr response: {response}")
        try:
            return int(value, 10)
        except ValueError as error:
            raise EmbeddedWorkerError(
                f"invalid peek_pe_gpr response: {response}"
            ) from error

    def peek_selected_pe(self) -> int:
        """Return the ASL memory-agent currently selected for the VM."""

        response = self.request("peek_pe")
        prefix, _, value = response.partition(" ")
        if prefix != "value":
            raise EmbeddedWorkerError(f"unexpected peek_pe response: {response}")
        try:
            return int(value, 10)
        except ValueError as error:
            raise EmbeddedWorkerError(
                f"invalid peek_pe response: {response}"
            ) from error

    def peek_terminal_pending(self) -> bool:
        """Observe ASL's post-ACRC terminal marker without changing state."""

        response = self.request("peek_terminal_pending")
        prefix, _, value = response.partition(" ")
        if prefix != "value":
            raise EmbeddedWorkerError(
                f"unexpected peek_terminal_pending response: {response}"
            )
        try:
            marker = int(value, 10)
        except ValueError as error:
            raise EmbeddedWorkerError(
                f"invalid peek_terminal_pending response: {response}"
            ) from error
        if marker not in {0, 1}:
            raise EmbeddedWorkerError(f"invalid terminal marker: {marker}")
        return bool(marker)

    def _peek_flag(self, command: str) -> bool:
        response = self.request(command)
        prefix, _, value = response.partition(" ")
        if prefix != "value":
            raise EmbeddedWorkerError(f"unexpected {command} response: {response}")
        try:
            marker = int(value, 10)
        except ValueError as error:
            raise EmbeddedWorkerError(
                f"invalid {command} response: {response}"
            ) from error
        if marker not in {0, 1}:
            raise EmbeddedWorkerError(f"invalid {command} marker: {marker}")
        return bool(marker)

    def peek_bundle_active(self) -> bool:
        """Observe whether ASL currently owns an open bundle."""

        return self._peek_flag("peek_bundle_active")

    def peek_bundle_body_active(self) -> bool:
        """Observe whether ASL currently executes a bundle body."""

        return self._peek_flag("peek_bundle_body_active")

    def peek_acr(self) -> int:
        """Read the ASL-owned current access-control ring."""

        response = self.request("peek_acr")
        prefix, _, value = response.partition(" ")
        if prefix != "value":
            raise EmbeddedWorkerError(f"unexpected peek_acr response: {response}")
        try:
            return int(value, 10)
        except ValueError as error:
            raise EmbeddedWorkerError(
                f"invalid peek_acr response: {response}"
            ) from error

    def peek_control_request(self) -> int:
        """Read the ASL-owned request operand published by ACRC/control ops."""

        response = self.request("peek_control_request")
        prefix, _, value = response.partition(" ")
        if prefix != "value":
            raise EmbeddedWorkerError(
                f"unexpected peek_control_request response: {response}"
            )
        try:
            return int(value, 10)
        except ValueError as error:
            raise EmbeddedWorkerError(
                f"invalid peek_control_request response: {response}"
            ) from error

    def peek_barg_bpcn(self) -> int:
        """Read ASL's pending bundle continuation candidate."""

        response = self.request("peek_barg_bpcn")
        prefix, _, value = response.partition(" ")
        if prefix != "value":
            raise EmbeddedWorkerError(f"unexpected peek_barg_bpcn response: {response}")
        try:
            return int(value, 10)
        except ValueError as error:
            raise EmbeddedWorkerError(
                f"invalid peek_barg_bpcn response: {response}"
            ) from error

    def peek_barg_word(self) -> int:
        """Read the packed ASL BARG control word for diagnostics."""
        response = self.request("peek_barg_word")
        prefix, _, value = response.partition(" ")
        if prefix != "value":
            raise EmbeddedWorkerError(f"unexpected peek_barg_word response: {response}")
        try:
            return int(value, 10)
        except ValueError as error:
            raise EmbeddedWorkerError(
                f"invalid peek_barg_word response: {response}"
            ) from error

    def peek_shared_flags(self, shared_id: int) -> int:
        response = self.request(f"peek_shared {shared_id}")
        prefix, _, value = response.partition(" ")
        if prefix != "value":
            raise EmbeddedWorkerError(f"unexpected peek_shared response: {response}")
        return int(value, 10)

    def set_tpc(self, value: int) -> None:
        """Set ASL's shared TPC before executing a selected PE's instruction."""

        if not isinstance(value, int) or value < 0 or value >= (1 << 64):
            raise ValueError("TPC must fit in 64 bits")
        response = self.request(f"set_tpc 0x{value:x}")
        if response != "status 0":
            raise EmbeddedWorkerError(f"unexpected set_tpc response: {response}")

    def reset(self) -> None:
        if self.request("reset") != "status 0":
            raise EmbeddedWorkerError("embedded ASL reset failed")

    def stop(self) -> None:
        with self._lock:
            process = self._process
            self._process = None
            if process is None:
                return
            try:
                if process.poll() is None and process.stdin is not None:
                    process.stdin.write("quit\n")
                    process.stdin.flush()
                    process.wait(timeout=min(self.timeout_s, 5.0))
            except (BrokenPipeError, OSError, subprocess.TimeoutExpired):
                self._terminate(process)
            finally:
                if process.poll() is None:
                    self._terminate(process)
                for stream in (process.stdin, process.stdout, process.stderr):
                    try:
                        stream.close() if stream is not None else None
                    except OSError:
                        pass
                if self._stderr_thread is not None:
                    self._stderr_thread.join(timeout=1.0)
                    self._stderr_thread = None
                if self._runtime_dir is not None:
                    shutil.rmtree(self._runtime_dir, ignore_errors=True)
                    self._runtime_dir = None
                self._initial_source = None

    close = stop

    def __enter__(self) -> "EmbeddedAslWorker":
        self.start()
        return self

    def __exit__(self, _type: Any, _value: Any, _traceback: Any) -> None:
        self.stop()

    def _ensure_built(self, spec: Path | None = None) -> tuple[WorkerIdentity, Path]:
        spec = Path(spec or self.pto_spec_root / "build" / "pto-spec.asl").resolve()
        pin = self.pto_spec_root / ".aslref-version"
        aslref_root = Path(
            os.environ.get(
                "PTO_ASLREF_ROOT", str(self.pto_spec_root / ".cache" / "herdtools7")
            )
        ).resolve()
        build = aslref_root / "_build" / "default" / "asllib"
        cmxa = build / "asllib.cmxa"
        byte_cmi = build / ".asllib.objs" / "byte"
        native_cmi = build / ".asllib.objs" / "native"
        if not spec.is_file() or not pin.is_file():
            raise EmbeddedWorkerError("generated ASL artifact or ASLRef pin is missing")
        if not (aslref_root / ".git").exists():
            raise EmbeddedWorkerError("ASLRef checkout is not a Git worktree")
        if not cmxa.is_file() or not byte_cmi.is_dir() or not native_cmi.is_dir():
            raise EmbeddedWorkerError("ASLRef native asllib artifacts are missing")
        wrapper = Path(__file__).with_name("Worker.ml")
        compiler = self._find_tool("ocamlopt")
        aslref_commit = pin.read_text(encoding="utf-8").strip()
        current_commit = self._run_git(aslref_root, "rev-parse", "HEAD")
        if current_commit != aslref_commit:
            raise EmbeddedWorkerError(
                "ASLRef checkout does not match the pinned commit: "
                f"expected {aslref_commit}, got {current_commit}"
            )
        if self._run_git(aslref_root, "status", "--porcelain"):
            raise EmbeddedWorkerError("ASLRef checkout is not clean")
        origin = self._run_git(aslref_root, "remote", "get-url", "origin")
        expected_origin = "https://github.com/herd/herdtools7.git"
        origin_pin = self.pto_spec_root / ".aslref-origin"
        if origin_pin.is_file():
            expected_origin = origin_pin.read_text(encoding="utf-8").strip()
        if origin != expected_origin:
            raise EmbeddedWorkerError(
                "ASLRef checkout has an unexpected origin: "
                f"expected {expected_origin}, got {origin}"
            )
        wrapper_sha = _sha256(wrapper)
        spec_sha = _sha256(spec)
        asllib_sha = _sha256(cmxa)
        compiler_version = self._run_tool(compiler, "-version").strip()
        cache_key = _digest(
            [
                WORKER_BUILD_SCHEMA,
                aslref_commit,
                asllib_sha,
                wrapper_sha,
                spec_sha,
                compiler_version,
            ]
        )
        target = self.cache_root / cache_key
        executable = target / "aslref-worker"
        metadata = target / "identity.json"
        identity = self._cached_identity(executable, metadata, cache_key)
        if identity is not None:
            return identity, executable
        target.mkdir(parents=True, exist_ok=True)
        lock_path = target / "build.lock"
        with lock_path.open("a+b") as lock_file:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
            identity = self._cached_identity(executable, metadata, cache_key)
            if identity is not None:
                return identity, executable
            build_dir = Path(tempfile.mkdtemp(prefix="build-", dir=target))
            try:
                source = build_dir / "Worker.ml"
                built_executable = build_dir / "aslref-worker"
                built_metadata = build_dir / "identity.json"
                shutil.copy2(wrapper, source)
                self._compile(
                    compiler,
                    build_dir,
                    source,
                    built_executable,
                    cmxa,
                    byte_cmi,
                    native_cmi,
                )
                worker_sha256 = _sha256(built_executable)
                identity = WorkerIdentity(
                    cache_key=cache_key,
                    aslref_commit=aslref_commit,
                    asllib_sha256=asllib_sha,
                    worker_sha256=worker_sha256,
                    spec_sha256=spec_sha,
                    wrapper_sha256=wrapper_sha,
                )
                built_metadata.write_text(
                    json.dumps(identity.as_dict(), indent=2, sort_keys=True) + "\n",
                    encoding="utf-8",
                )
                os.replace(built_executable, executable)
                os.replace(built_metadata, metadata)
                published = self._cached_identity(executable, metadata, cache_key)
                if published is None:
                    raise EmbeddedWorkerError(
                        "published embedded worker identity does not match executable"
                    )
                return published, executable
            finally:
                shutil.rmtree(build_dir, ignore_errors=True)

    @staticmethod
    def _cached_identity(
        executable: Path, metadata: Path, cache_key: str
    ) -> WorkerIdentity | None:
        if not (
            executable.is_file()
            and os.access(executable, os.X_OK)
            and metadata.is_file()
        ):
            return None
        try:
            identity = WorkerIdentity(
                **json.loads(metadata.read_text(encoding="utf-8"))
            )
            if identity.cache_key == cache_key and identity.worker_sha256 == _sha256(
                executable
            ):
                return identity
        except (OSError, TypeError, ValueError, json.JSONDecodeError):
            pass
        return None

    def _compile(
        self,
        compiler: Path,
        cwd: Path,
        source: Path,
        executable: Path,
        cmxa: Path,
        byte_cmi: Path,
        native_cmi: Path,
    ) -> None:
        ocaml_root = compiler.parent.parent
        zarith = ocaml_root / "lib" / "zarith"
        menhir = ocaml_root / "lib" / "menhirLib"
        dependencies = (zarith / "zarith.cmxa", menhir / "menhirLib.cmxa")
        if not all(path.is_file() for path in dependencies):
            raise EmbeddedWorkerError(
                "OCaml zarith/menhirLib native libraries are missing"
            )
        command = [str(compiler), "-ccopt", "-L" + str(self._gmp_library_dir())]
        if sys.platform == "darwin":
            command.extend(("-cclib", "-Wl,-stack_size,0x20000000"))
        for path in (byte_cmi, native_cmi, zarith, menhir):
            command.extend(("-I", str(path)))
        command.extend(
            (
                "-o",
                str(executable),
                *(str(path) for path in dependencies),
                str(cmxa),
                str(source),
            )
        )
        completed = subprocess.run(
            command, cwd=cwd, env=self._environment(), capture_output=True, text=True
        )
        if completed.returncode != 0 or not executable.is_file():
            detail = (completed.stderr or completed.stdout).strip()
            raise EmbeddedWorkerError(
                "failed to build embedded ASLRef worker"
                + (f": {detail[-4000:]}" if detail else "")
            )
        executable.chmod(0o755)

    @staticmethod
    def _find_tool(name: str) -> Path:
        candidates = []
        found = shutil.which(name)
        if found:
            candidates.append(Path(found))
        candidates += [
            Path.home() / ".opam-pto" / "pto-ocaml" / "bin" / name,
            Path.home() / ".opam" / "default" / "bin" / name,
        ]
        for candidate in candidates:
            if candidate.is_file() and os.access(candidate, os.X_OK):
                return candidate.resolve()
        raise EmbeddedWorkerError(f"required tool is unavailable: {name}")

    @staticmethod
    def _run_tool(tool: Path, *args: str) -> str:
        completed = subprocess.run(
            [str(tool), *args],
            env=EmbeddedAslWorker._environment(),
            capture_output=True,
            text=True,
        )
        if completed.returncode:
            raise EmbeddedWorkerError(
                f"failed to run {tool}: {completed.stderr.strip()}"
            )
        return completed.stdout

    @staticmethod
    def _run_git(root: Path, *args: str) -> str:
        completed = subprocess.run(
            ["git", "-C", str(root), *args],
            capture_output=True,
            text=True,
        )
        if completed.returncode != 0:
            raise EmbeddedWorkerError(
                f"failed to inspect ASLRef checkout: {completed.stderr.strip()}"
            )
        return completed.stdout.strip()

    @staticmethod
    def _gmp_library_dir() -> Path:
        for path in (
            Path.home() / ".local" / "lib",
            Path("/usr/lib/x86_64-linux-gnu"),
            Path("/lib/x86_64-linux-gnu"),
        ):
            if (path / "libgmp.so").is_file() or (path / "libgmp.a").is_file():
                return path
        return Path("/usr/lib/x86_64-linux-gnu")

    @staticmethod
    def _environment() -> dict[str, str]:
        environment = runtime_environment()
        environment["LD_LIBRARY_PATH"] = (
            str(Path.home() / ".local" / "lib")
            + os.pathsep
            + environment.get("LD_LIBRARY_PATH", "")
        )
        return environment

    def _readline(self, stream: Any) -> str | None:
        selector = selectors.DefaultSelector()
        try:
            selector.register(stream, selectors.EVENT_READ)
            if not selector.select(self.timeout_s):
                if self._process is not None:
                    self._terminate(self._process)
                raise EmbeddedWorkerError(
                    f"embedded ASL worker timed out after {self.timeout_s:g}s"
                )
            line = stream.readline()
            return line.rstrip("\r\n") if line else None
        finally:
            selector.close()

    def _drain_stderr(self, stream: Any) -> None:
        try:
            for line in iter(stream.readline, ""):
                self._stderr_lines.append(line.rstrip("\r\n"))
        except (OSError, ValueError):
            pass

    def _failure(self, prefix: str) -> str:
        details = "\n".join(self._stderr_lines[-20:])
        return f"{prefix}: {details}" if details else prefix

    @staticmethod
    def _terminate(process: subprocess.Popen[str]) -> None:
        if process.poll() is not None:
            return
        try:
            os.killpg(process.pid, signal.SIGTERM)
            process.wait(timeout=2.0)
        except (ProcessLookupError, subprocess.TimeoutExpired):
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            try:
                process.wait(timeout=2.0)
            except subprocess.TimeoutExpired:
                pass


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _digest(parts: list[str]) -> str:
    digest = hashlib.sha256()
    for part in parts:
        encoded = part.encode("utf-8")
        digest.update(len(encoded).to_bytes(8, "big"))
        digest.update(encoded)
    return digest.hexdigest()
