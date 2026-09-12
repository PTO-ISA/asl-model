#!/usr/bin/env python3
"""Evaluate ASL worker startup and full FA execution performance."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import resource
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from asl_model.runtime.multi_elf import AslMultiPeElfRunner

if __package__:
    from tools.generate_smoke_elf import build as build_smoke_elf
else:
    from generate_smoke_elf import build as build_smoke_elf


EXPECTED_FA_SHA256 = "d5bad30e5cddc41a01ca098b33a070488e419a6f755516ae54e17614a9cced2c"
EXPECTED_FA_STEPS = 325
EXPECTED_FAULT_CODE = 11
EXPECTED_FAULT_PC = 0x11432
EXPECTED_TRACE_SHA256 = (
    "6887a559d9e33415c1ccc910598a898a13736c6ddc7e1d8907b659ff08a56d5c"
)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _rss_mib() -> float:
    value = resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss
    scale = 1024 * 1024 if sys.platform == "darwin" else 1024
    return value / scale


def _trace_sha256(steps) -> str:
    fields = (
        "pe_id",
        "thread_id",
        "address",
        "instruction",
        "length_bits",
        "status",
        "returncode",
        "next_pc",
        "finished",
        "fault_code",
    )
    trace = [{name: getattr(step, name) for name in fields} for step in steps]
    payload = json.dumps(trace, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _signature_matches(result, trace_sha256: str) -> bool:
    last = result.steps[-1] if result.steps else None
    return bool(
        last is not None
        and len(result.steps) == EXPECTED_FA_STEPS
        and result.termination == "step_failed"
        and last.fault_code == EXPECTED_FAULT_CODE
        and last.address == EXPECTED_FAULT_PC
        and trace_sha256 == EXPECTED_TRACE_SHA256
    )


def _parallel_execution_matches(result, *, final_round_faults: bool) -> bool:
    parallel = result.runtime_metrics.get("parallel", {})
    rounds = parallel.get("rounds")
    commits = parallel.get("commits")
    if not isinstance(rounds, int) or isinstance(rounds, bool) or rounds <= 0:
        return False
    return bool(
        parallel.get("attempted") is True
        and parallel.get("fallback") is False
        and commits == rounds - int(final_round_faults)
    )


def _git(root: Path, *args: str) -> str:
    return subprocess.check_output(["git", "-C", str(root), *args], text=True).strip()


def _source_identity(spec: Path, aslref: Path) -> dict[str, object]:
    repository = Path(__file__).resolve().parents[1]
    staged_diff = subprocess.check_output(
        ["git", "-C", str(repository), "diff", "--cached", "--binary"]
    )
    unstaged_clean = (
        subprocess.run(
            ["git", "-C", str(repository), "diff", "--quiet"], check=False
        ).returncode
        == 0
    )
    return {
        "repository_head": _git(repository, "rev-parse", "HEAD"),
        "repository_index_tree": _git(repository, "write-tree"),
        "staged_diff_sha256": hashlib.sha256(staged_diff).hexdigest(),
        "unstaged_clean": unstaged_clean,
        "pto_spec_commit": _git(spec, "rev-parse", "HEAD"),
        "aslref_commit": _git(aslref, "rev-parse", "HEAD"),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pto-spec", required=True, type=Path)
    parser.add_argument("--aslref", required=True, type=Path)
    parser.add_argument("--elf", required=True, type=Path)
    parser.add_argument("--cache-root", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--max-startup-s", type=float, default=15.0)
    parser.add_argument("--max-fa-s", type=float, default=180.0)
    parser.add_argument("--timeout-s", type=float, default=150.0)
    args = parser.parse_args()

    spec = args.pto_spec.resolve()
    aslref = args.aslref.resolve()
    os.environ["PTO_ASLREF_ROOT"] = str(aslref)
    elf = args.elf.resolve()
    cache = args.cache_root.resolve()
    output = args.output.resolve()
    if _sha256(elf) != EXPECTED_FA_SHA256:
        raise SystemExit("FA ELF SHA-256 does not match the frozen evaluator input")

    smoke = cache / "inputs" / "scalar-add.elf"
    build_smoke_elf(spec, smoke)
    runner_args = {
        "pto_spec_root": spec,
        "timeout_s": args.timeout_s,
        "model_profile": "linx-runtime",
        "worker_scope": "per-pe",
        "parallel_pe_steps": True,
        "cache_root": cache,
        "expected_machine": 0xE9,
    }

    started = time.perf_counter()
    startup_result = AslMultiPeElfRunner(**runner_args).run(
        smoke, pe_count=4, max_instructions=4
    )
    startup_seconds = time.perf_counter() - started
    startup_rss_mib = _rss_mib()

    started = time.perf_counter()
    fa_result = AslMultiPeElfRunner(**runner_args).run(
        elf, pe_count=4, max_instructions=2000
    )
    fa_seconds = time.perf_counter() - started
    fa_rss_mib = _rss_mib()
    last = fa_result.steps[-1] if fa_result.steps else None
    trace_sha256 = _trace_sha256(fa_result.steps)
    slowest_steps = sorted(
        fa_result.steps, key=lambda step: step.elapsed_ms, reverse=True
    )[:12]

    signature_matches = _signature_matches(fa_result, trace_sha256)
    passed = bool(
        startup_result.ok
        and startup_seconds <= args.max_startup_s
        and _parallel_execution_matches(startup_result, final_round_faults=False)
        and fa_seconds <= args.max_fa_s
        and signature_matches
        and _parallel_execution_matches(fa_result, final_round_faults=True)
    )
    source_identity = _source_identity(spec, aslref)
    passed = passed and bool(source_identity["unstaged_clean"])
    report = {
        "schema": "pto-asl-model-fa-performance-v1",
        "status": "pass" if passed else "fail",
        "recorded_at": datetime.now(timezone.utc).isoformat(),
        "command": sys.argv,
        "source_identity": source_identity,
        "thresholds": {
            "startup_seconds": args.max_startup_s,
            "fa_seconds": args.max_fa_s,
        },
        "startup": {
            "seconds": startup_seconds,
            "rss_mib": startup_rss_mib,
            "steps": len(startup_result.steps),
            "ok": startup_result.ok,
            "runtime_metrics": dict(startup_result.runtime_metrics),
        },
        "fa": {
            "elf_sha256": EXPECTED_FA_SHA256,
            "seconds": fa_seconds,
            "rss_mib": fa_rss_mib,
            "steps": len(fa_result.steps),
            "termination": fa_result.termination,
            "last_fault_code": last.fault_code if last else None,
            "last_pc": last.address if last else None,
            "signature_matches": signature_matches,
            "trace_sha256": trace_sha256,
            "expected_trace_sha256": EXPECTED_TRACE_SHA256,
            "worker_identity": dict(fa_result.artifact),
            "runtime_metrics": dict(fa_result.runtime_metrics),
            "slowest_steps": [
                {
                    "pe_id": step.pe_id,
                    "pc": step.address,
                    "instruction": step.instruction,
                    "width": step.length_bits,
                    "status": step.status,
                    "fault_code": step.fault_code,
                    "elapsed_ms": step.elapsed_ms,
                }
                for step in slowest_steps
            ],
        },
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
