from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class ModelCase:
    case_id: str
    instruction: str
    instruction_class: str
    source: Path
    test: Path


class CaseRegistry:
    """Declarative mapping from model case IDs to canonical ASL tests."""

    def __init__(self, path: Path, pto_spec_root: Path):
        self.path = path
        self.pto_spec_root = pto_spec_root
        data = json.loads(path.read_text(encoding="utf-8"))
        if data.get("schema") != "pto.asl-model-cases.v2":
            raise ValueError(f"unsupported case registry schema: {path}")
        rows = data.get("cases")
        if not isinstance(rows, list) or not rows:
            raise ValueError(f"case registry must contain cases: {path}")
        self._cases: dict[str, ModelCase] = {}
        for row in rows:
            if not isinstance(row, dict):
                raise ValueError(f"case entry must be an object: {path}")
            case_id = self._string(row, "id")
            if case_id in self._cases:
                raise ValueError(f"duplicate case ID: {case_id}")
            self._cases[case_id] = ModelCase(
                case_id=case_id,
                instruction=self._string(row, "instruction"),
                instruction_class=self._string(row, "class"),
                source=Path(self._string(row, "source")),
                test=Path(self._string(row, "test")),
            )

    @staticmethod
    def _string(row: dict[str, object], field: str) -> str:
        value = row.get(field)
        if not isinstance(value, str) or not value:
            raise ValueError(f"case field {field} must be a non-empty string")
        return value

    def ids(self) -> tuple[str, ...]:
        return tuple(sorted(self._cases))

    def get(self, case_id: str) -> ModelCase:
        try:
            return self._cases[case_id]
        except KeyError as error:
            raise KeyError(f"unknown ASL model case: {case_id}") from error

    def validate(self) -> None:
        for case in self._cases.values():
            if case.source.is_absolute() or case.test.is_absolute():
                raise ValueError(f"case paths must be relative: {case.case_id}")
            source = self.pto_spec_root / case.source
            test = self.pto_spec_root / case.test
            if not source.is_file():
                raise FileNotFoundError(f"missing ASL source for {case.case_id}: {source}")
            if not test.is_file():
                raise FileNotFoundError(f"missing ASL test for {case.case_id}: {test}")
            records = [
                json.loads(line[len("// PTO-TEST: ") :])
                for line in test.read_text(encoding="utf-8").splitlines()
                if line.startswith("// PTO-TEST: ")
            ]
            if len(records) != 1 or records[0].get("kind") != "execution":
                raise ValueError(f"case test must have one execution PTO-TEST: {test}")
            if records[0].get("source") != case.source.as_posix():
                raise ValueError(f"case source mismatch for {case.case_id}: {test}")
