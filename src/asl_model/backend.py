from __future__ import annotations

import subprocess
import tempfile
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Callable, Mapping, Protocol

from .artifact import AslArtifact
from .cases import CaseRegistry, ModelCase
from .session import AslSession, EmbeddedAslSession, ProcessAslSession
from .paths import runtime_environment


@dataclass(frozen=True)
class BackendResult:
    """Stable result envelope shared by every functional backend.

    ``status`` describes the backend outcome, not whether the instruction is
    architecturally legal.  A canonical ASL test that exits zero is
    ``passed``; a backend that has not been implemented is ``unavailable``.
    In particular, an unavailable native backend must never be represented as
    a successful comparison.

    ``observations`` is deliberately backend-neutral.  It is empty for the
    current ASL process runner because canonical tests assert their state
    internally.  A future stateful backend should populate it with a
    canonical architectural snapshot before participating in differential
    comparison.
    """

    backend: str
    case_id: str
    instruction: str
    status: str
    returncode: int
    artifact: dict[str, str]
    observations: Mapping[str, Any] | None = None
    error: str | None = None
    comparable: bool = False

    def as_dict(self) -> dict[str, object]:
        return {
            "backend": self.backend,
            "case_id": self.case_id,
            "instruction": self.instruction,
            "status": self.status,
            "returncode": self.returncode,
            "artifact": self.artifact,
            "observations": dict(self.observations or {}),
            "error": self.error,
            "comparable": self.comparable,
        }

    @property
    def succeeded(self) -> bool:
        """Whether this backend completed its requested operation."""

        return self.returncode == 0 and self.status in {"passed", "executed"}


class FunctionalBackend(Protocol):
    name: str

    def execute(self, case: ModelCase) -> BackendResult:
        ...

    def availability(self) -> "BackendAvailability":
        ...


@dataclass(frozen=True)
class BackendAvailability:
    """Explicit capability state used by CLI and orchestration code."""

    backend: str
    available: bool
    reason: str | None = None

    def as_dict(self) -> dict[str, object]:
        payload: dict[str, object] = {
            "backend": self.backend,
            "available": self.available,
        }
        if self.reason:
            payload["reason"] = self.reason
        return payload


class AslBackend:
    """ASL-only backend; no instruction semantics are implemented in Python."""

    name = "asl"

    def __init__(self, pto_spec_root: Path, registry: CaseRegistry):
        self.pto_spec_root = pto_spec_root
        self.registry = registry
        self.registry.validate()
        self.artifact = AslArtifact.load(pto_spec_root)
        self.aslref = pto_spec_root / "scripts" / "aslref"
        if not self.aslref.is_file():
            raise FileNotFoundError(f"missing ASLRef launcher: {self.aslref}")

    def execute(self, case: ModelCase) -> BackendResult:
        registered = self.registry.get(case.case_id)
        if registered != case:
            raise ValueError(f"case does not match registry: {case.case_id}")
        test_path = self.pto_spec_root / case.test
        source = (self.pto_spec_root / "build" / "pto-spec.asl").read_text(encoding="utf-8")
        source += f"\n// ASL model case: {case.case_id}\n"
        source += test_path.read_text(encoding="utf-8")
        with tempfile.TemporaryDirectory(prefix="pto-asl-model-") as directory:
            input_file = Path(directory) / "case.asl"
            input_file.write_text(source, encoding="utf-8")
            completed = subprocess.run(
                [str(self.aslref), "--type-check-no-warn", str(input_file)],
                cwd=self.pto_spec_root,
                env=self._environment(),
                capture_output=True,
                text=True,
            )
        return BackendResult(
            backend=self.name,
            case_id=case.case_id,
            instruction=case.instruction,
            status="passed" if completed.returncode == 0 else "failed",
            returncode=completed.returncode,
            artifact=self.artifact.as_dict(),
            error=(completed.stderr.strip() or None) if completed.returncode else None,
            # ASLRef currently evaluates assertions inside the canonical test;
            # it does not expose a state snapshot through this process ABI.
            comparable=False,
        )

    def availability(self) -> BackendAvailability:
        return BackendAvailability(self.name, True)

    def create_session(self, initial_source: str = "") -> AslSession:
        """Create a stateful ASL session behind the backend-neutral contract."""
        return ProcessAslSession.create(self.pto_spec_root, initial_source)

    def create_embedded_session(
        self,
        initial_source: str = "",
        *,
        timeout_s: float = 120.0,
        cache_root: Path | None = None,
    ) -> AslSession:
        """Create the persistent ASL VM session.

        The process-backed source session remains available for compatibility;
        instruction-oriented validation should use this method so ASLRef is
        parsed and type-checked once and then kept alive across steps.
        """

        return EmbeddedAslSession.create(
            self.pto_spec_root,
            initial_source,
            timeout_s=timeout_s,
            cache_root=cache_root,
        )

    @staticmethod
    def _environment() -> dict[str, str]:
        return runtime_environment()


class NativeBackend:
    """Reserved high-throughput backend boundary.

    The native implementation intentionally does not exist yet.  Keeping the
    stub explicit lets callers wire backend selection today without silently
    treating a missing implementation as a passing result.  Tests and a
    future C++ adapter may inject ``executor`` while the public protocol stays
    unchanged.
    """

    name = "native"

    def __init__(
        self,
        executor: Callable[[ModelCase], BackendResult] | None = None,
        artifact: Mapping[str, str] | None = None,
    ):
        self._executor = executor
        self._artifact = dict(artifact or {})

    def availability(self) -> BackendAvailability:
        if self._executor is None:
            return BackendAvailability(
                self.name,
                False,
                "native backend is reserved for the future high-throughput implementation",
            )
        return BackendAvailability(self.name, True)

    def execute(self, case: ModelCase) -> BackendResult:
        if self._executor is None:
            return BackendResult(
                backend=self.name,
                case_id=case.case_id,
                instruction=case.instruction,
                status="unavailable",
                returncode=3,
                artifact=self._artifact,
                error=self.availability().reason,
            )
        result = self._executor(case)
        if not isinstance(result, BackendResult):
            raise TypeError("native executor must return BackendResult")
        return replace(result, backend=self.name)


class DifferentialBackend:
    """Run two backends and compare their observable architectural results."""

    name = "differential"

    def __init__(self, reference: FunctionalBackend, candidate: FunctionalBackend):
        self.reference = reference
        self.candidate = candidate

    def availability(self) -> BackendAvailability:
        reference = self.reference.availability()
        candidate = self.candidate.availability()
        if reference.available and candidate.available:
            return BackendAvailability(self.name, True)
        reasons = [item.reason for item in (reference, candidate) if item.reason]
        return BackendAvailability(self.name, False, "; ".join(reasons) or "backend unavailable")

    def execute(self, case: ModelCase) -> BackendResult:
        reference = self.reference.execute(case)
        candidate = self.candidate.execute(case)
        artifact = reference.artifact or candidate.artifact
        details = {
            "reference": reference.as_dict(),
            "candidate": candidate.as_dict(),
        }
        if not reference.succeeded or not candidate.succeeded:
            status = "unavailable" if "unavailable" in {reference.status, candidate.status} else "failed"
            return BackendResult(
                backend=self.name,
                case_id=case.case_id,
                instruction=case.instruction,
                status=status,
                returncode=3 if status == "unavailable" else 1,
                artifact=artifact,
                observations=details,
                error="both backends must complete successfully before comparison",
            )
        if not reference.comparable or not candidate.comparable:
            return BackendResult(
                backend=self.name,
                case_id=case.case_id,
                instruction=case.instruction,
                status="inconclusive",
                returncode=4,
                artifact=artifact,
                observations=details,
                error="backend result lacks a comparable architectural snapshot",
            )
        if reference.artifact != candidate.artifact:
            return BackendResult(
                backend=self.name,
                case_id=case.case_id,
                instruction=case.instruction,
                status="mismatch",
                returncode=1,
                artifact=artifact,
                observations=details,
                error="backend artifact identities differ",
            )
        if dict(reference.observations or {}) != dict(candidate.observations or {}):
            return BackendResult(
                backend=self.name,
                case_id=case.case_id,
                instruction=case.instruction,
                status="mismatch",
                returncode=1,
                artifact=artifact,
                observations=details,
                error="architectural observations differ",
            )
        return BackendResult(
            backend=self.name,
            case_id=case.case_id,
            instruction=case.instruction,
            status="passed",
            returncode=0,
            artifact=artifact,
            observations=details,
            comparable=True,
        )
