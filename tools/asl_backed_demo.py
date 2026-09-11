#!/usr/bin/env python3
"""Run canonical PTO ASL execution tests as a functional-model demo.

The runner is instruction-agnostic: adding a supported instruction means
adding an ASL test under pto-spec/tests/asl, not adding a semantic handler here.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
import tempfile
from pathlib import Path

from asl_model.paths import resolve_pto_spec, runtime_environment


REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_PTO_SPEC_ROOT = resolve_pto_spec()
CASES_FILE = REPO_ROOT / "src" / "asl_model" / "cases.json"
TEST_PREFIX = "// PTO-TEST: "


def canonical_tests() -> dict[str, Path]:
    """Load the extensible representative-case registry."""
    data = json.loads(CASES_FILE.read_text(encoding="utf-8"))
    if data.get("schema") not in {"pto.asl-model-cases.v1", "pto.asl-model-cases.v2"}:
        raise RuntimeError(f"unsupported case registry schema: {CASES_FILE}")
    cases = data.get("cases")
    if not isinstance(cases, list):
        raise RuntimeError(f"invalid case registry: {CASES_FILE}")
    result: dict[str, Path] = {}
    for case in cases:
        if not isinstance(case, dict) or not isinstance(case.get("instruction"), str):
            raise RuntimeError(f"invalid case entry: {CASES_FILE}")
        instruction = case["instruction"]
        test = case.get("test")
        if not isinstance(test, str) or instruction in result:
            raise RuntimeError(f"invalid or duplicate case: {instruction}")
        result[instruction] = Path(test)
    return result


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def artifact_manifest(pto_spec_root: Path) -> dict[str, object]:
    spec_file = pto_spec_root / "build" / "pto-spec.asl"
    decoder_file = pto_spec_root / "build" / "decoders.asl"
    source_order_file = pto_spec_root / "build" / "asl-source-order.txt"
    aslref = pto_spec_root / "scripts" / "aslref"
    paths = (spec_file, decoder_file, source_order_file)
    missing = [str(path) for path in paths if not path.is_file()]
    if missing:
        raise RuntimeError("missing generated PTO ASL inputs: " + ", ".join(missing))
    if not aslref.is_file():
        raise RuntimeError(f"missing ASLRef launcher: {aslref}")
    commit = subprocess.run(
        ["git", "-C", str(pto_spec_root), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    return {
        "schema": "pto.asl-backed-demo.v2",
        "pto_spec_commit": commit,
        "spec_sha256": sha256(spec_file),
        "decoder_sha256": sha256(decoder_file),
        "source_order_sha256": sha256(source_order_file),
        "semantic_entry": "ExecutePTOInstruction",
        "runtime_abi": "aslref-process-v1",
    }


def execute_test(instruction: str, pto_spec_root: Path) -> tuple[int, dict[str, object]]:
    cases = canonical_tests()
    if instruction not in cases:
        raise ValueError(f"unsupported demo instruction: {instruction}")
    test_path = pto_spec_root / cases[instruction]
    if not test_path.is_file():
        raise RuntimeError(f"missing canonical ASL test: {test_path}")
    manifest = artifact_manifest(pto_spec_root)
    spec_file = pto_spec_root / "build" / "pto-spec.asl"
    aslref = pto_spec_root / "scripts" / "aslref"
    source = spec_file.read_text(encoding="utf-8")
    source += f"\n// Canonical ASL test: {cases[instruction].as_posix()}\n"
    source += test_path.read_text(encoding="utf-8")
    with tempfile.TemporaryDirectory(prefix="pto-asl-demo-") as directory:
        input_file = Path(directory) / "test.asl"
        input_file.write_text(source, encoding="utf-8")
        result = subprocess.run(
            [str(aslref), "--type-check-no-warn", str(input_file)],
            cwd=pto_spec_root,
            env=runtime_environment(),
        )
    return result.returncode, {
        "instruction": instruction,
        "test_source": cases[instruction].as_posix(),
        "status": "executed" if result.returncode == 0 else "rejected_or_failed",
        "returncode": result.returncode,
        "manifest": manifest,
    }


def run_test(instruction: str, pto_spec_root: Path) -> int:
    returncode, payload = execute_test(instruction, pto_spec_root)
    print(json.dumps(payload, indent=2))
    return returncode


def check_cases(pto_spec_root: Path) -> dict[str, str]:
    cases = canonical_tests()
    registry = json.loads(CASES_FILE.read_text(encoding="utf-8"))
    registry_cases = registry.get("cases", [])
    registered_names = {case.get("instruction") for case in registry_cases if isinstance(case, dict)}
    registry_sources = {
        case["instruction"]: case["source"]
        for case in registry_cases
        if isinstance(case, dict) and isinstance(case.get("instruction"), str) and isinstance(case.get("source"), str)
    }
    if registered_names != set(cases):
        raise RuntimeError("case registry and ASL execution-test inventory disagree")
    for instruction, relative_path in cases.items():
        path = pto_spec_root / relative_path
        if not path.is_file():
            raise RuntimeError(f"missing canonical ASL test for {instruction}: {path}")
        records = [
            json.loads(line[len(TEST_PREFIX) :])
            for line in path.read_text(encoding="utf-8").splitlines()
            if line.startswith(TEST_PREFIX)
        ]
        if len(records) != 1 or records[0].get("kind") != "execution":
            raise RuntimeError(f"case test must contain one execution PTO-TEST record: {path}")
        expected_source = registry_sources[instruction]
        if records[0].get("source") != expected_source:
            raise RuntimeError(f"case source mismatch for {instruction}: {path}")
    artifact_manifest(pto_spec_root)
    return {instruction: path.as_posix() for instruction, path in cases.items()}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--instruction")
    parser.add_argument("--pto-spec", type=Path, default=DEFAULT_PTO_SPEC_ROOT)
    parser.add_argument("--manifest", action="store_true")
    parser.add_argument("--list", action="store_true", help="list ASL-backed instruction tests")
    parser.add_argument("--check", action="store_true", help="validate the demo inventory without executing ASLRef")
    args = parser.parse_args()
    pto_spec_root = args.pto_spec.expanduser().resolve()
    try:
        if args.list:
            print(json.dumps({key: value.as_posix() for key, value in canonical_tests().items()}, indent=2))
            return 0
        if args.check:
            print(json.dumps({"status": "ready", "cases": check_cases(pto_spec_root)}, indent=2))
            return 0
        if args.manifest:
            print(json.dumps(artifact_manifest(pto_spec_root), indent=2))
            return 0
        if not args.instruction:
            parser.error("--instruction is required unless --manifest or --list is used")
        return run_test(args.instruction, pto_spec_root)
    except (OSError, RuntimeError, ValueError, subprocess.CalledProcessError) as error:
        print(f"asl-backed demo: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
