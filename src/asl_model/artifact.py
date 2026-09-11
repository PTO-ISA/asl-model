from __future__ import annotations

import hashlib
import subprocess
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class AslArtifact:
    pto_spec_commit: str
    spec_sha256: str
    decoder_sha256: str
    source_order_sha256: str

    @classmethod
    def load(cls, pto_spec_root: Path) -> "AslArtifact":
        build = pto_spec_root / "build"
        paths = {
            "spec_sha256": build / "pto-spec.asl",
            "decoder_sha256": build / "decoders.asl",
            "source_order_sha256": build / "asl-source-order.txt",
        }
        missing = [str(path) for path in paths.values() if not path.is_file()]
        if missing:
            raise FileNotFoundError("missing ASL build artifacts: " + ", ".join(missing))
        commit = subprocess.run(
            ["git", "-C", str(pto_spec_root), "rev-parse", "HEAD"],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
        return cls(
            pto_spec_commit=commit,
            **{field: _sha256(path) for field, path in paths.items()},
        )

    def as_dict(self) -> dict[str, str]:
        return {
            "pto_spec_commit": self.pto_spec_commit,
            "spec_sha256": self.spec_sha256,
            "decoder_sha256": self.decoder_sha256,
            "source_order_sha256": self.source_order_sha256,
        }


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()
