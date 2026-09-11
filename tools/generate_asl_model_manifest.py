#!/usr/bin/env python3
"""Generate a machine-readable inventory from PTO ASL instruction metadata.

The inventory is a projection of ASL metadata. It is deliberately not a
semantic implementation and must never be edited by hand.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
from pathlib import Path

from asl_model.paths import resolve_pto_spec


REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_PTO_SPEC_ROOT = resolve_pto_spec()


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def git_commit(pto_spec_root: Path) -> str:
    return subprocess.run(
        ["git", "-C", str(pto_spec_root), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def parse_instruction(path: Path, pto_spec_root: Path) -> dict[str, object] | None:
    prefix = "// PTO-INSTRUCTION: "
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.startswith(prefix):
            value = json.loads(line[len(prefix) :])
            if not isinstance(value, dict):
                raise ValueError(f"{path}: PTO-INSTRUCTION must be an object")
            records = value.get("catalog_records", [])
            if not isinstance(records, list):
                raise ValueError(f"{path}: catalog_records must be an array")
            records = records or [value]
            if any(not isinstance(record, dict) for record in records):
                raise ValueError(f"{path}: catalog records must be objects")
            mnemonic = value.get("mnemonic", records[0].get("mnemonic"))
            if not isinstance(mnemonic, str) or not mnemonic:
                raise ValueError(f"{path}: catalog record has no mnemonic")
            forms = []
            for record in records:
                form_mnemonic = record.get("mnemonic", mnemonic)
                if form_mnemonic != mnemonic:
                    raise ValueError(f"{path}: catalog records disagree on mnemonic")
                forms.append(
                    {
                        "semantic_handler": record.get("semantic_handler", value.get("semantic_handler")),
                        "semantic_family": record.get("semantic_family", value.get("semantic_family")),
                        "length_bits": record.get("length_bits", value.get("length_bits")),
                        "status": record.get("status", value.get("status")),
                        "form_id": record.get("form_id", value.get("form_id")),
                    }
                )
            return {
                "mnemonic": mnemonic,
                "source": path.relative_to(pto_spec_root).as_posix(),
                "forms": forms,
            }
    return None


def generate(pto_spec_root: Path, cases_file: Path) -> dict[str, object]:
    asl_root = pto_spec_root / "asl"
    if not asl_root.is_dir():
        raise RuntimeError(f"missing ASL root: {asl_root}")
    instructions = []
    for path in sorted(asl_root.rglob("*.asl")):
        record = parse_instruction(path, pto_spec_root)
        if record is not None:
            instructions.append(record)
    instructions.sort(key=lambda item: (str(item["mnemonic"]), str(item["source"])))
    by_mnemonic: dict[str, list[dict[str, object]]] = {}
    for record in instructions:
        by_mnemonic.setdefault(str(record["mnemonic"]), []).append(record)
    return {
        "schema": "pto.asl-model-manifest.v1",
        "pto_spec_commit": git_commit(pto_spec_root),
        "case_registry_sha256": sha256(cases_file),
        "instruction_count": len(instructions),
        "instructions": instructions,
        "notes": {
            "semantic_owner": "pto-spec/asl",
            "execution_entry": "ExecutePTOInstruction",
            "generated_projection": True,
            "duplicate_mnemonics": sorted(
                mnemonic for mnemonic, records in by_mnemonic.items() if len(records) > 1
            ),
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--pto-spec", type=Path, default=DEFAULT_PTO_SPEC_ROOT)
    parser.add_argument(
        "--cases", type=Path,
        default=REPO_ROOT / "src" / "asl_model" / "cases.json",
    )
    args = parser.parse_args()
    result = generate(
        args.pto_spec.expanduser().resolve(), args.cases.expanduser().resolve()
    )
    payload = json.dumps(result, indent=2) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(payload, encoding="utf-8")
    else:
        print(payload, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
