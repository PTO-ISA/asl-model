#!/usr/bin/env python3
"""Generate a minimal PTO ELF from one accepted catalog encoding.

The generator reads the selected instruction encoding from PTO-SPEC instead
of embedding an opcode in ASL-MODEL. It is intended only for runtime smoke
tests and provides no numerical correctness claim.
"""

from __future__ import annotations

import argparse
import json
import struct
from pathlib import Path


ELF_HEADER = struct.Struct("<16sHHIQQQIHHHHHH")
PROGRAM_HEADER = struct.Struct("<IIQQQQQQ")
PTO_ELF_MACHINE = 0xE9


def catalog_encoding(pto_spec: Path, mnemonic: str) -> tuple[int, int, str]:
    """Return one unconstrained accepted direct scalar encoding."""

    catalog = pto_spec / "spec" / "catalog" / "scalar-forms.json"
    value = json.loads(catalog.read_text(encoding="utf-8"))
    matches = [
        form for form in value.get("forms", [])
        if form.get("mnemonic") == mnemonic
        and form.get("status") == "accepted"
        and form.get("constraints") == []
        and len(form.get("encoding", [])) == 1
    ]
    if len(matches) != 1:
        raise ValueError(
            f"expected one unconstrained accepted {mnemonic} form, found {len(matches)}"
        )
    form = matches[0]
    encoding = form["encoding"][0]
    width = encoding.get("width_bits")
    if width not in {16, 32, 48, 64}:
        raise ValueError(f"unsupported smoke encoding width: {width!r}")
    mask = int(encoding["mask"], 0)
    match = int(encoding["match"], 0)
    if match & mask != match or match >= 1 << width:
        raise ValueError("catalog match is outside its declared mask or width")
    return match, width, str(form["form_id"])


def build(pto_spec: Path, output: Path, mnemonic: str = "ADD") -> dict[str, object]:
    """Write a one-instruction ELF64 PT_LOAD carrier."""

    instruction, width, form_id = catalog_encoding(pto_spec, mnemonic)
    code = instruction.to_bytes(width // 8, "little")
    entry = 0x1000
    file_offset = ELF_HEADER.size + PROGRAM_HEADER.size
    identification = b"\x7fELF" + bytes([2, 1, 1]) + bytes(9)
    header = ELF_HEADER.pack(
        identification,
        2,
        PTO_ELF_MACHINE,
        1,
        entry,
        ELF_HEADER.size,
        0,
        0,
        ELF_HEADER.size,
        PROGRAM_HEADER.size,
        1,
        0,
        0,
        0,
    )
    program = PROGRAM_HEADER.pack(
        1, 5, file_offset, entry, entry, len(code), len(code), 0x1000
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_bytes(header + program + code)
    return {
        "output": str(output),
        "mnemonic": mnemonic,
        "form_id": form_id,
        "encoding": f"0x{instruction:0{width // 4}x}",
        "width_bits": width,
        "entry_point": entry,
        "machine": PTO_ELF_MACHINE,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pto-spec", required=True, type=Path)
    parser.add_argument("--mnemonic", default="ADD")
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    print(json.dumps(
        build(args.pto_spec.resolve(), args.output.resolve(), args.mnemonic),
        sort_keys=True,
    ))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
