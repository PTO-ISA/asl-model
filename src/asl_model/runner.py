"""Batch validation orchestration for ASL-owned model cases."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from .backend import BackendResult, FunctionalBackend
from .cases import CaseRegistry


@dataclass(frozen=True)
class BatchReport:
    backend: str
    results: tuple[BackendResult, ...]

    @property
    def passed(self) -> int:
        return sum(result.succeeded for result in self.results)

    @property
    def failed(self) -> int:
        return len(self.results) - self.passed

    def as_dict(self) -> dict[str, object]:
        return {
            "schema": "pto.asl-model-batch-report.v1",
            "backend": self.backend,
            "summary": {
                "total": len(self.results),
                "passed": self.passed,
                "failed": self.failed,
            },
            "results": [result.as_dict() for result in self.results],
        }

    def write_json(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(self.as_dict(), indent=2) + "\n", encoding="utf-8")


def run_cases(
    backend: FunctionalBackend,
    registry: CaseRegistry,
    case_ids: Iterable[str] | None = None,
) -> BatchReport:
    selected = tuple(case_ids) if case_ids is not None else registry.ids()
    results = tuple(backend.execute(registry.get(case_id)) for case_id in selected)
    return BatchReport(backend=backend.name, results=results)
