"""Host completion policies derived from the ASL instruction catalog."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class CompletionEncoding:
    mnemonic: str
    width_bits: int
    mask: int
    match: int


class AslCompletionPolicy:
    """Recognize host termination encodings without implementing semantics."""

    def __init__(self, pto_spec_root: Path, *, enabled: bool = True):
        self.enabled = enabled
        self.encodings = self._load_encodings(Path(pto_spec_root)) if enabled else ()

    def is_terminal(self, instruction: int, length_bits: int) -> bool:
        return any(
            length_bits == encoding.width_bits
            and instruction & encoding.mask == encoding.match
            for encoding in self.encodings
        )

    @staticmethod
    def _load_encodings(root: Path) -> tuple[CompletionEncoding, ...]:
        encodings: list[CompletionEncoding] = []
        for path in sorted((root / "asl").rglob("*.asl")):
            for line in path.read_text(encoding="utf-8").splitlines():
                prefix = "// PTO-INSTRUCTION: "
                if not line.startswith(prefix):
                    continue
                record = json.loads(line[len(prefix) :])
                # Older generated catalogs put the mnemonic on the outer
                # instruction record, while current PTO-SPEC puts it on each
                # entry in ``catalog_records``.  Treat both layouts as the
                # same source of truth.  In particular, do not discard a
                # valid current record just because the outer object only
                # has assembly/catalog metadata.
                forms = list(record.get("catalog_records", []))
                if record.get("mnemonic") == "ACRC":
                    forms.insert(0, record)
                found_acrc = False
                for form in forms:
                    if form.get("mnemonic") != "ACRC":
                        continue
                    found_acrc = True
                    for encoding in form.get("encoding", []):
                        encodings.append(
                            CompletionEncoding(
                                mnemonic="ACRC",
                                width_bits=int(encoding["width_bits"]),
                                mask=int(encoding["mask"], 0),
                                match=int(encoding["match"], 0),
                            )
                        )
                if found_acrc:
                    break
        if not encodings:
            raise FileNotFoundError("ASL catalog contains no ACRC encoding")
        return tuple(encodings)
