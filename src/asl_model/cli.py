from __future__ import annotations

import argparse
import json
from pathlib import Path

from .backend import AslBackend, DifferentialBackend, FunctionalBackend, NativeBackend
from .cases import CaseRegistry
from .runner import run_cases
from .runtime.asl_elf import AslElfRunner
from .runtime.multi_elf import AslMultiPeElfRunner, UnsupportedPeStateScope
from .session import SessionError
from .paths import resolve_pto_spec


DEFAULT_PTO_SPEC_ROOT = resolve_pto_spec()
REGISTRY = Path(__file__).with_name("cases.json")


def registry(pto_spec_root: Path = DEFAULT_PTO_SPEC_ROOT) -> CaseRegistry:
    return CaseRegistry(REGISTRY, pto_spec_root)


def backend(name: str, pto_spec_root: Path = DEFAULT_PTO_SPEC_ROOT) -> FunctionalBackend:
    registry = CaseRegistry(REGISTRY, pto_spec_root)
    if name == "native":
        return NativeBackend()
    asl = AslBackend(pto_spec_root, registry)
    if name == "asl":
        return asl
    if name == "differential":
        return DifferentialBackend(asl, NativeBackend(artifact=asl.artifact.as_dict()))
    raise ValueError(f"unknown backend: {name}")


def main() -> int:
    parser = argparse.ArgumentParser(description="ASL-owned functional validation backend")
    parser.add_argument("command", choices=("list", "manifest", "check", "run", "run-all", "session", "elf-run"))
    parser.add_argument(
        "--pto-spec",
        type=Path,
        default=DEFAULT_PTO_SPEC_ROOT,
        help="path to the PTO ASL specification checkout (or PTO_SPEC_ROOT)",
    )
    parser.add_argument(
        "--cache-root", type=Path, default=None,
        help="model-owned cache directory (or ASL_MODEL_CACHE_ROOT)",
    )
    parser.add_argument("--elf", type=Path, help="ELF path for elf-run")
    parser.add_argument("--start-symbol", help="optional ELF symbol to use instead of the ELF entry")
    parser.add_argument("--model-base", type=lambda value: int(value, 0), help="optional ASL model relocation base for ELF")
    parser.add_argument(
        "--expected-machine",
        type=lambda value: int(value, 0),
        default=None,
        help="optional ELF e_machine value (for PTO ELF use 0xe9)",
    )
    parser.add_argument("--model-profile", choices=("portable", "linx-runtime"), default="portable", help="explicit ASL model profile for elf-run")
    parser.add_argument("--stack-policy", choices=("explicit", "after-image", "disabled"), default="after-image", help="guest stack layout policy for elf-run")
    parser.add_argument("--stack-top", type=lambda value: int(value, 0), default=None, help="guest stack top (required by explicit policy)")
    parser.add_argument("--stack-size", type=lambda value: int(value, 0), default=0x01000000, help="guest stack mapping size for elf-run")
    parser.add_argument("--stack-gap", type=lambda value: int(value, 0), default=0x1000, help="gap between image/stacks and between stack banks")
    parser.add_argument("--stack-stride", type=lambda value: int(value, 0), default=None, help="distance between PE stack tops")
    parser.add_argument("--red-zone", type=lambda value: int(value, 0), default=16, help="bytes reserved below each stack top")
    parser.add_argument("--length-bits", choices=("auto", "16", "32", "48", "64"), default="auto", help="instruction width for elf-run")
    parser.add_argument("--max-instructions", type=int, default=1, help="maximum fetched instructions for elf-run")
    parser.add_argument("--pe-count", type=int, default=1, help="logical PE/thread contexts for elf-run")
    parser.add_argument(
        "--worker-scope",
        choices=("per-pe", "spmd", "core", "single"),
        default="per-pe",
        help=("ASL worker scope: spmd uses one VM for the core and applies each "
              "instruction for its PE; per-pe starts one VM per PE; core is an "
              "explicit incomplete diagnostic experiment; single is a "
              "one-context capability probe"),
    )
    parser.add_argument(
        "--experimental-core",
        action="store_true",
        help="allow incomplete one-worker core scope for diagnostics only",
    )
    parser.add_argument(
        "--parallel-pe-steps",
        action="store_true",
        help=("execute per-PE rounds concurrently with transactional shared "
              "memory and automatic serial fallback on conflicts"),
    )
    parser.add_argument("--completion-policy", choices=("direct-boot", "none"), default="direct-boot", help="host completion policy for elf-run")
    parser.add_argument("--case", help="case ID for the run command")
    parser.add_argument("--output", type=Path, help="write a batch report to this JSON path")
    parser.add_argument(
        "--step",
        action="append",
        default=[],
        help="ASL statement block for a process session, or encoded instruction source for embedded mode (repeatable)",
    )
    parser.add_argument(
        "--initial-source",
        default="",
        help="ASL statements executed before session steps",
    )
    parser.add_argument(
        "--timeout-s",
        type=float,
        default=120.0,
        help="per-step ASLRef timeout for the session command",
    )
    parser.add_argument(
        "--backend",
        choices=("asl", "native", "differential"),
        default="asl",
        help="execution backend (default: asl)",
    )
    parser.add_argument(
        "--session-mode",
        choices=("process", "embedded"),
        default="process",
        help="session implementation for the session command (default: process)",
    )
    args = parser.parse_args()
    pto_spec_root = args.pto_spec.expanduser().resolve()
    try:
        if args.command == "elf-run":
            if args.backend != "asl":
                parser.error("elf-run currently supports only --backend asl")
            if args.elf is None:
                parser.error("elf-run requires --elf PATH")
            if args.pe_count <= 0:
                parser.error("--pe-count must be positive")
            if args.worker_scope == "core" and not args.experimental_core:
                parser.error("--worker-scope core requires --experimental-core")
            from .runtime.completion import AslCompletionPolicy
            completion = AslCompletionPolicy(pto_spec_root, enabled=args.completion_policy == "direct-boot")
            if args.stack_policy != "disabled" and args.stack_size <= 0:
                parser.error("--stack-size must be positive unless stack policy is disabled")
            if args.stack_policy == "explicit" and (args.stack_top is None or args.stack_top <= 0):
                parser.error("--stack-top must be positive with explicit stack policy")
            # A single PE uses the direct ELF runner so symbol-based starts
            # (for example ``main``) and its ASL-owned PC loop are preserved
            # for every profile.  The multi-PE scheduler is only needed when
            # more than one architectural context is requested.
            if args.pe_count == 1:
                result = AslElfRunner(pto_spec_root, completion_policy=completion, model_base=args.model_base, cache_root=args.cache_root).run(
                    args.elf,
                    length_bits=None if args.length_bits == "auto" else int(args.length_bits),
                    max_instructions=args.max_instructions,
                    initial_source=args.initial_source,
                    start_symbol=args.start_symbol,
                    model_profile=args.model_profile,
                    stack_top=args.stack_top,
                    stack_size=args.stack_size,
                    stack_policy=args.stack_policy,
                    stack_gap=args.stack_gap,
                    stack_stride=args.stack_stride,
                    red_zone=args.red_zone,
                    expected_machine=args.expected_machine,
                )
            else:
                result = AslMultiPeElfRunner(
                    pto_spec_root,
                    completion_policy=completion,
                    model_base=args.model_base,
                    worker_scope=args.worker_scope,
                    experimental_core=args.experimental_core,
                    parallel_pe_steps=args.parallel_pe_steps,
                    model_profile=args.model_profile,
                    cache_root=args.cache_root,
                    stack_pointer=args.stack_top,
                    stack_size=args.stack_size,
                    stack_policy=args.stack_policy,
                    stack_gap=args.stack_gap,
                    stack_stride=args.stack_stride,
                    red_zone=args.red_zone,
                    expected_machine=args.expected_machine,
                ).run(
                    args.elf,
                    pe_count=args.pe_count,
                    length_bits=None if args.length_bits == "auto" else int(args.length_bits),
                    max_instructions=args.max_instructions,
                    initial_source=args.initial_source,
                )
            print(json.dumps(result.as_dict(), indent=2))
            complete = getattr(result, "complete", getattr(result, "ok", False))
            return 0 if complete else 1
        # Case-registry construction is intentionally kept off the ELF path:
        # an ELF smoke run only needs the generated ASL and ASLRef worker, and
        # should not fail because the optional semantic-case corpus is absent.
        instance = backend(args.backend, pto_spec_root)
        cases = registry(pto_spec_root)
        if args.command == "list":
            for case_id in cases.ids():
                case = cases.get(case_id)
                print(f"{case_id}\t{case.instruction}\t{case.instruction_class}")
            return 0
        if args.command == "manifest":
            asl = AslBackend(pto_spec_root, cases)
            print(json.dumps(asl.artifact.as_dict(), indent=2))
            return 0
        if args.command == "check":
            availability = instance.availability()
            payload = {
                "status": "ready" if availability.available else "unavailable",
                "backend": args.backend,
                "availability": availability.as_dict(),
                "cases": list(cases.ids()),
            }
            print(json.dumps(payload, indent=2))
            return 0 if availability.available else 3
        if args.command == "run-all":
            report = run_cases(instance, cases)
            if args.output:
                report.write_json(args.output)
            print(json.dumps(report.as_dict(), indent=2))
            return 0 if report.failed == 0 else 1
        if args.command == "session":
            if args.backend != "asl":
                raise ValueError("session command currently supports only --backend asl")
            if args.session_mode == "embedded":
                session = instance.create_embedded_session(
                    args.initial_source, timeout_s=args.timeout_s, cache_root=args.cache_root
                )
            else:
                session = instance.create_session(args.initial_source)
            try:
                if not args.step:
                    print(json.dumps({
                        "status": "ready",
                        "backend": session.backend_name,
                        "artifact": session.artifact.as_dict(),
                    }, indent=2))
                    return 0
                results = [
                    session.step(source, label=f"cli-step-{index}").as_dict()
                    for index, source in enumerate(args.step, 1)
                ]
                succeeded = all(item["returncode"] == 0 for item in results)
                print(json.dumps({
                    "status": "passed" if succeeded else "failed",
                    "backend": session.backend_name,
                    "steps": results,
                }, indent=2))
                return 0 if succeeded else (results[-1]["returncode"] or 1)
            finally:
                session.destroy()
        if not args.case:
            parser.error("run requires --case CASE_ID")
        result = instance.execute(cases.get(args.case))
        print(json.dumps(result.as_dict(), indent=2))
        return result.returncode
    except (FileNotFoundError, KeyError, ValueError, OSError, SessionError, UnsupportedPeStateScope) as error:
        print(json.dumps({"status": "backend_error", "error": str(error)}, indent=2))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
