"""Exact-identity compile-to-ASL closure orchestration."""

from __future__ import annotations

import argparse
import datetime
import hashlib
import json
import os
import pathlib
import subprocess
import sys
import time
from collections.abc import Sequence

from .closure_artifacts import (
    ASLREF_COMMIT,
    CASE_SCHEMA,
    LOCK_SCHEMA,
    NDF_COMMIT,
    RUN_ENVELOPE_SCHEMA,
    SEMANTIC_PAYLOAD_SCHEMA,
    canonical_sha256,
    read_json,
    validate_case,
    validate_lock,
    validate_request,
    validate_run_envelope,
    validate_semantic_payload,
    write_canonical_json,
)
from .elf_note import parse_pto_isa_note
from .runner import (
    ASLRefTimeoutError,
    PTO_ELF_MACHINE,
    RunConfiguration,
    _unique_symbol,
    parse_elf,
)


MODEL_ABI = "pto-asl-model-experimental-v2"
WORKER_PROTOCOL = "pto-asl-worker-v1"
CASE_FILE = "case.yaml"


def _model_run_lock(
    release_identity: dict[str, object],
    pto_identity: dict[str, str],
    aslref_identity: dict[str, str],
) -> dict[str, object]:
    """Derive the runner lock from this candidate, never the committed baseline."""
    return {
        "schema": "pto-asl-model-lock-v1",
        "pto_repository": pto_identity["repository"],
        "pto_ref": pto_identity["commit"],
        "pto_commit": pto_identity["commit"],
        "pto_tree": pto_identity["tree"],
        "aslref_repository": aslref_identity["repository"],
        "aslref_commit": aslref_identity["commit"],
        "architecture_version": release_identity["release"],
        "publication_version": release_identity["publication_version"],
        "encoding_abi": release_identity["encoding_abi"],
        "encoding_projection_sha256": release_identity["encoding_projection_sha256"],
        "model_abi": MODEL_ABI,
        "worker_protocol": WORKER_PROTOCOL,
    }


def _sha256(path: pathlib.Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _git(root: pathlib.Path, *arguments: str) -> str:
    completed = subprocess.run(
        ["git", "-C", str(root), *arguments], text=True,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False,
    )
    if completed.returncode:
        raise ValueError(completed.stderr.strip() or f"git {' '.join(arguments)} failed")
    return completed.stdout.strip()


def _checkout_identity(candidate: dict[str, object], label: str) -> dict[str, str]:
    root = pathlib.Path(str(candidate["path"])).resolve()
    if not (root / ".git").exists():
        raise ValueError(f"{label} is not a git checkout: {root}")
    if _git(root, "status", "--porcelain"):
        raise ValueError(f"{label} checkout is dirty")
    commit = _git(root, "rev-parse", "HEAD")
    expected_commit = str(candidate["commit"])
    if commit != expected_commit:
        raise ValueError(f"{label} commit mismatch: expected {expected_commit}, got {commit}")
    repository = _git(root, "remote", "get-url", "origin")
    if repository != candidate["repository"]:
        raise ValueError(f"{label} origin mismatch: expected {candidate['repository']}, got {repository}")
    return {
        "repository": repository,
        "commit": commit,
        "tree": _git(root, "rev-parse", "HEAD^{tree}"),
    }


def _dependency_identity(root: pathlib.Path, repository: str, commit: str, label: str) -> dict[str, str]:
    if not root.exists():
        raise ValueError(f"missing {label} checkout: {root}")
    actual_commit = _git(root, "rev-parse", "HEAD")
    actual_repository = _git(root, "remote", "get-url", "origin")
    if actual_commit != commit or actual_repository != repository:
        raise ValueError(f"{label} checkout identity mismatch")
    if _git(root, "status", "--porcelain"):
        raise ValueError(f"{label} checkout is dirty")
    return {"repository": repository, "commit": commit, "tree": _git(root, "rev-parse", "HEAD^{tree}")}


def _tool(path: pathlib.Path, root: pathlib.Path, label: str) -> dict[str, str]:
    resolved = path.resolve()
    if not resolved.is_file() or not os.access(resolved, os.X_OK):
        raise ValueError(f"{label} is not an executable file: {resolved}")
    try:
        relative = resolved.relative_to(root.resolve())
        logical_path = "source/" + relative.as_posix()
    except ValueError:
        build_root = next(
            (parent for parent in (resolved.parent, *resolved.parents)
             if (parent / "CMakeCache.txt").is_file()),
            None,
        )
        if build_root is None:
            raise ValueError(f"{label} is neither in the source checkout nor a verified build tree")
        cache = (build_root / "CMakeCache.txt").read_text(encoding="utf-8", errors="replace")
        expected_source = (root.resolve() / "llvm").as_posix()
        if f"LLVM_SOURCE_DIR:STATIC={expected_source}" not in cache:
            raise ValueError(f"{label} build tree does not identify the pinned LLVM checkout")
        logical_path = "build/" + resolved.relative_to(build_root).as_posix()
    return {"path": logical_path, "sha256": _sha256(resolved)}


def _invocation_path(path: pathlib.Path) -> pathlib.Path:
    """Return an absolute command path without dereferencing driver symlinks."""

    return pathlib.Path(os.path.abspath(path))


def _load_cases(root: pathlib.Path) -> dict[str, tuple[pathlib.Path, dict[str, object]]]:
    cases: dict[str, tuple[pathlib.Path, dict[str, object]]] = {}
    for metadata_path in sorted(root.glob(f"*/{CASE_FILE}")):
        # JSON is deliberately used as the deterministic YAML 1.2 subset.
        metadata = validate_case(read_json(metadata_path))
        case_id = str(metadata["case_id"])
        if metadata_path.parent.name != case_id:
            raise ValueError(f"{metadata_path}: directory and case_id differ")
        if case_id in cases:
            raise ValueError(f"duplicate AVS case_id: {case_id}")
        source = metadata_path.parent / str(metadata["source"])
        linker = metadata_path.parent / str(metadata["linker_script"])
        golden = metadata_path.parent / str(metadata["golden"]["path"])  # type: ignore[index]
        if not source.is_file() or not linker.is_file() or not golden.is_file():
            raise ValueError(f"{case_id}: source, linker script, or golden file is missing")
        if _sha256(golden) != metadata["golden"]["sha256"]:  # type: ignore[index]
            raise ValueError(f"{case_id}: independent golden digest mismatch")
        cases[case_id] = (metadata_path.parent, metadata)
    if not cases:
        raise ValueError("AVS corpus contains no cases")
    return cases


def _corpus_digest(cases: dict[str, tuple[pathlib.Path, dict[str, object]]]) -> str:
    projection: dict[str, str] = {}
    for case_id, (root, metadata) in sorted(cases.items()):
        for name in (CASE_FILE, str(metadata["source"]), str(metadata["linker_script"]), str(metadata["golden"]["path"])):  # type: ignore[index]
            projection[f"{case_id}/{name}"] = _sha256(root / name)
    return canonical_sha256(projection)


def _load_impact(path: pathlib.Path) -> tuple[dict[str, object], str, list[str]]:
    document = read_json(path)
    if (
        document.get("schema_version") != "0.1"
        or document.get("command") != "impact pto-release"
        or document.get("ok") is not True
        or document.get("diagnostics") != []
    ):
        raise ValueError("NDF impact command did not produce a clean v0.1 result")
    report = document.get("data")
    if not isinstance(report, dict) or report.get("schema_version") != "1":
        raise ValueError("NDF PTO release impact report schema mismatch")
    changes = report.get("changes")
    targets = report.get("conformance_targets")
    if not isinstance(changes, list) or not isinstance(targets, list):
        raise ValueError("NDF impact changes or conformance targets are missing")
    conformance_targets = {
        item.get("target_uri") for item in targets
        if isinstance(item, dict)
        and isinstance(item.get("consumer_uri"), str)
        and item["consumer_uri"].startswith("ndf://asl-model/")
        and isinstance(item.get("target_uri"), str)
    }
    affected_uris: set[str] = set()
    for item in changes:
        if not isinstance(item, dict) or not isinstance(item.get("uri"), str):
            raise ValueError("NDF impact contains a malformed change")
        uri = item["uri"]
        if uri in conformance_targets or (
            uri.startswith("ndf://pto-spec/PTO-INST-")
            and item.get("kind") in {"added", "modified", "moved"}
        ):
            affected_uris.add(uri)
    affected = sorted(uri.removeprefix("ndf://pto-spec/") for uri in affected_uris)
    return report, canonical_sha256(report), affected


def _select_cases(
    cases: dict[str, tuple[pathlib.Path, dict[str, object]]],
    affected: list[str], mandatory: list[str],
) -> tuple[list[str], list[str]]:
    unknown_mandatory = sorted(set(mandatory) - set(cases))
    if unknown_mandatory:
        raise ValueError("unknown mandatory cases: " + ", ".join(unknown_mandatory))
    selected = set(mandatory)
    uncovered: list[str] = []
    for pto_id in affected:
        matches = [case_id for case_id, (_, case) in cases.items() if pto_id in case["pto_ids"]]
        if not matches:
            uncovered.append(pto_id)
            continue
        selected.update(matches)
    if uncovered:
        raise ValueError(
            "affected PTO identities have no AVS case: " + ", ".join(uncovered)
        )
    if not selected:
        raise ValueError("closure selected no AVS cases")
    obligations = sorted({
        obligation for case_id in selected
        for obligation in cases[case_id][1]["obligation_ids"]
    })
    if not obligations:
        raise ValueError("selected cases provide no obligations")
    return sorted(selected), obligations


def _expand(arguments: list[str], values: dict[str, str]) -> list[str]:
    expanded: list[str] = []
    for argument in arguments:
        try:
            expanded.append(argument.format_map(values))
        except KeyError as error:
            raise ValueError(f"unknown command placeholder: {error.args[0]}") from error
    return expanded


class CaseStageTimeout(RuntimeError):
    def __init__(self, case_id: str, stage: str, timeout_seconds: float) -> None:
        self.case_id = case_id
        self.stage = stage
        self.failure_class = "timeout"
        super().__init__(
            f"case={case_id} stage={stage} failure_class=timeout "
            f"deadline exceeded after {timeout_seconds:.3f} seconds"
        )


def _remaining_timeout(deadline: float, case_id: str, stage: str) -> float:
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise CaseStageTimeout(case_id, stage, 0.0)
    return remaining


def _run_model_case(
    case_id: str, timeout_seconds: float, configuration: RunConfiguration,
) -> dict[str, object]:
    from .runner import run as run_model
    try:
        return run_model(configuration)
    except ASLRefTimeoutError as error:
        raise CaseStageTimeout(case_id, "execute", timeout_seconds) from error


def _run_command(
    command: list[str], timeout: float, case_id: str, stage: str,
) -> None:
    try:
        completed = subprocess.run(
            command, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            check=False, timeout=timeout,
        )
    except subprocess.TimeoutExpired as error:
        raise CaseStageTimeout(case_id, stage, timeout) from error
    if completed.returncode:
        diagnostics = "\n".join(part for part in (completed.stdout.strip(), completed.stderr.strip()) if part)
        raise RuntimeError(
            f"case={case_id} stage={stage} failure_class=failed "
            f"exit={completed.returncode}: {diagnostics[-4000:]}"
        )


def _golden_bytes(path: pathlib.Path, metadata: dict[str, object]) -> bytes:
    golden = metadata["golden"]
    assert isinstance(golden, dict)
    content = path.read_bytes()
    if golden["encoding"] == "raw":
        return content
    try:
        return bytes.fromhex(content.decode("ascii"))
    except (UnicodeDecodeError, ValueError) as error:
        raise ValueError(f"{path}: invalid hexadecimal golden data") from error


def _sidecar(
    case_id: str, case: dict[str, object], elf: pathlib.Path,
    golden: pathlib.Path, pto_commit: str,
) -> dict[str, object]:
    image = parse_elf(elf)
    execution = case["execution"]
    assert isinstance(execution, dict)
    symbols = {
        name: _unique_symbol(image, str(execution[name]))
        for name in ("start_symbol", "return_symbol", "stop_symbol", "result_symbol", "result_size_symbol")
    }
    result = symbols["result_symbol"]
    size = symbols["result_size_symbol"]
    model = case["model"]
    assert isinstance(model, dict)
    return {
        "schema": "pto-asl-elf-sidecar-v1",
        "case_id": case_id,
        "identity": {"pto_commit": pto_commit},
        "elf": {
            "path": elf.name, "sha256": image.sha256, "machine": PTO_ELF_MACHINE,
            "entry": image.entry,
            "segments": [{
                "address": segment.address, "filesz": len(segment.data),
                "memsz": segment.memory_size, "flags": segment.flags,
            } for segment in image.segments],
        },
        "model": {
            "profile": "bounded-reference-v1", "pe_count": 1,
            "memory_bytes": model["memory_bytes"], "tile_elements": model["tile_elements"],
            "runtime_typecheck": model["runtime_typecheck"],
        },
        "start": {
            "symbol": execution["start_symbol"], "pc": symbols["start_symbol"].value,
            "acr": execution["start_acr"],
            "return_symbol": execution["return_symbol"], "return_pc": symbols["return_symbol"].value,
        },
        "execution": {
            "stop_symbol": execution["stop_symbol"], "stop_pc": symbols["stop_symbol"].value,
            "stop_after_hits": execution["stop_after_hits"], "max_steps": execution["max_steps"],
            "stack_top": execution["stack_top"],
            "host_request": execution["host_request"],
        },
        "result": {
            "symbol": execution["result_symbol"], "size_symbol": execution["result_size_symbol"],
            "address": result.value, "size": size.value,
            "segments": [{"offset": 0, "size": size.value, "dtype": "opaque-bytes", "shape": [size.value], "comparison": "exact"}],
            "golden": {"path": golden.name, "sha256": _sha256(golden)},
        },
    }


def _execute_case(
    case_id: str, root: pathlib.Path, case: dict[str, object], output: pathlib.Path,
    tools: dict[str, pathlib.Path], target: str, asl_spec: pathlib.Path,
    aslref: pathlib.Path, model_lock: pathlib.Path, pto_commit: str,
    closure_lock: dict[str, object],
) -> dict[str, object]:
    output.mkdir(parents=True, exist_ok=False)
    source = root / str(case["source"])
    golden = root / str(case["golden"]["path"])  # type: ignore[index]
    obj = output / "case.o"
    elf = output / "case.elf"
    linker_script = root / str(case["linker_script"])
    values = {
        "source": str(source), "object": str(obj), "elf": str(elf),
        "linker_script": str(linker_script), "target": target,
    }
    compile_spec = case["compile"]
    link_spec = case["link"]
    assert isinstance(compile_spec, dict) and isinstance(link_spec, dict)
    compile_command = [str(tools[str(compile_spec["tool"])]), *_expand(compile_spec["arguments"], values)]  # type: ignore[arg-type]
    link_command = [str(tools[str(link_spec["tool"])]), *_expand(link_spec["arguments"], values)]  # type: ignore[arg-type]
    timeout = int(case["timeout_seconds"])
    deadline = time.monotonic() + timeout
    _run_command(
        compile_command, _remaining_timeout(deadline, case_id, "compile"),
        case_id, "compile",
    )
    object_note = parse_pto_isa_note(obj)
    _run_command(
        link_command, _remaining_timeout(deadline, case_id, "link"),
        case_id, "link",
    )
    elf_note = parse_pto_isa_note(elf)
    if object_note.as_dict() != elf_note.as_dict():
        raise ValueError(f"{case_id}: final ELF identity drift")
    sidecar_path = output / "case.sidecar.json"
    result_path = output / "result.bin"
    run_manifest_path = output / "model-run.json"
    # Runner resolves golden relative to the sidecar.
    copied_golden = output / "golden.bin"
    copied_golden.write_bytes(_golden_bytes(golden, case))
    sidecar = _sidecar(case_id, case, elf, copied_golden, pto_commit)
    write_canonical_json(sidecar_path, sidecar)
    model = case["model"]
    assert isinstance(model, dict)
    configuration = RunConfiguration(
        asl_spec=asl_spec, aslref=aslref, elf=elf, stop_pc=0, max_steps=0,
        result_address=0, result_size=0, sidecar=sidecar_path,
        manifest_output=run_manifest_path, result_output=result_path,
        lock=model_lock, memory_backend=str(model["backend"]),
        deadline_monotonic=deadline,
    )
    run_manifest = _run_model_case(case_id, timeout, configuration)
    result = result_path.read_bytes()
    if result != copied_golden.read_bytes():
        raise AssertionError(f"{case_id}: result differs from independent golden")
    manifest = {
        "schema": "pto-closure-case-manifest-v1",
        "case_id": case_id,
        "pto_ids": case["pto_ids"], "obligation_ids": case["obligation_ids"],
        "avs_ids": case["avs_ids"],
        "expected_length_sequence": case["expected_length_sequence"],
        "lane": case["lane"], "profile": case["profile"],
        "resource_class": case["resource_class"],
        "timeout_seconds": case["timeout_seconds"],
        "execution_policy": case["execution"],
        "closure_lock_sha256": canonical_sha256(closure_lock),
        "repositories": closure_lock["repositories"],
        "tools": closure_lock["tools"],
        "target": closure_lock["target"],
        "commands": {
            "compile": {"tool": compile_spec["tool"], "arguments": compile_spec["arguments"]},
            "link": {"tool": link_spec["tool"], "arguments": link_spec["arguments"]},
            "run": {
                "tool": "pto-asl-run",
                "arguments": ["--sidecar", "case.sidecar.json", "--elf", "case.elf"],
            },
        },
        "digests": {
            "source": _sha256(source), "object": _sha256(obj), "elf": _sha256(elf),
            "elf_note_descriptor": elf_note.descriptor_sha256,
            "linker_script": _sha256(linker_script),
            "golden_source": _sha256(golden), "golden": _sha256(copied_golden),
            "result": _sha256(result_path),
            "sidecar": _sha256(sidecar_path),
            "model_run_semantic": canonical_sha256({
                key: value for key, value in run_manifest.items()
                if key != "host_timing_ms"
            }),
        },
        "golden_provenance": case["golden"]["provenance"],  # type: ignore[index]
        "terminal": {"status": "passed", "failure_class": "none", "final_tpc": run_manifest["final_tpc"]},
        "model": case["model"],
    }
    write_canonical_json(output / "case-manifest.json", manifest)
    return manifest


def run_closure(arguments: argparse.Namespace) -> tuple[dict[str, object], dict[str, object]]:
    request = validate_request(read_json(arguments.request))
    repositories = request["repositories"]
    assert isinstance(repositories, dict)
    identities = {name: _checkout_identity(candidate, name) for name, candidate in repositories.items()}  # type: ignore[arg-type]
    pto_root = pathlib.Path(str(repositories["pto_spec"]["path"])).resolve()  # type: ignore[index]
    model_root = pathlib.Path(str(repositories["asl_model"]["path"])).resolve()  # type: ignore[index]
    if model_root != pathlib.Path(__file__).resolve().parents[2]:
        raise ValueError("running ASL-MODEL checkout differs from closure request")
    ndf_pin_text = (pto_root / "ndf.lock").read_text(encoding="utf-8")
    if NDF_COMMIT not in ndf_pin_text:
        raise ValueError("PTO-SPEC ndf.lock does not pin the closure NDF commit")
    if (pto_root / ".aslref-version").read_text(encoding="utf-8").strip() != ASLREF_COMMIT:
        raise ValueError("PTO-SPEC ASLRef pin mismatch")
    ndf_identity = _dependency_identity(arguments.ndf_root, "https://github.com/PTO-ISA/normative_language.git", NDF_COMMIT, "normative_language")
    aslref_identity = _dependency_identity(arguments.aslref_root, "https://github.com/PTO-ISA/herdtools7.git", ASLREF_COMMIT, "ASLRef")
    llvm_root = pathlib.Path(str(repositories["llvm"]["path"])).resolve()  # type: ignore[index]
    tool_paths = {
        "clang": _invocation_path(arguments.clang),
        "llvm_mc": _invocation_path(arguments.llvm_mc),
        "ld_lld": _invocation_path(arguments.ld_lld),
        "aslref": _invocation_path(arguments.aslref),
    }
    tool_identities = {
        "clang": _tool(arguments.clang, llvm_root, "clang"),
        "llvm_mc": _tool(arguments.llvm_mc, llvm_root, "llvm-mc"),
        "ld_lld": _tool(arguments.ld_lld, llvm_root, "ld.lld"),
        "aslref": _tool(arguments.aslref, arguments.aslref_root, "aslref"),
    }
    cases = _load_cases(model_root / "avs" / "cases")
    _, impact_sha256, affected = _load_impact(arguments.ndf_impact)
    policy = request["policy"]
    assert isinstance(policy, dict)
    if affected != policy["affected_pto_ids"]:
        raise ValueError("request and NDF impact affected PTO IDs differ")
    selected_cases, obligations = _select_cases(cases, affected, policy["mandatory_case_ids"])  # type: ignore[arg-type]
    selected_backends = {cases[case_id][1]["model"]["backend"] for case_id in selected_cases}  # type: ignore[index]
    if selected_backends != {arguments.backend}:
        raise ValueError("selected case backends do not equal the requested closure backend")
    lock = {
        "schema": LOCK_SCHEMA, "identity": request["identity"],
        "repositories": {**identities, "normative_language": ndf_identity, "aslref": aslref_identity},
        "tools": tool_identities, "target": {"triple": arguments.target},
        "model": {
            "abi": MODEL_ABI, "worker_protocol": WORKER_PROTOCOL,
            "backend": arguments.backend, "profile": "bounded-reference-v1",
        },
        "corpus": {"sha256": _corpus_digest(cases), "case_ids": selected_cases},
        "obligations": {
            "affected_pto_ids": affected, "selected": obligations,
            "sha256": canonical_sha256(obligations),
        },
    }
    validate_lock(lock)
    output = arguments.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    write_canonical_json(output / "closure-lock.json", lock)
    model_run_lock = _model_run_lock(request["identity"], identities["pto_spec"], aslref_identity)  # type: ignore[arg-type]
    model_run_lock_path = output / "model-run-lock.json"
    write_canonical_json(model_run_lock_path, model_run_lock)
    manifests: list[dict[str, object]] = []
    for case_id in selected_cases:
        root, case = cases[case_id]
        manifests.append(_execute_case(
            case_id, root, case, output / "cases" / case_id, tool_paths,
            arguments.target, arguments.asl_spec, arguments.aslref,
            model_run_lock_path, identities["pto_spec"]["commit"], lock,
        ))
    case_digests = [{"case_id": item["case_id"], "sha256": canonical_sha256(item)} for item in manifests]
    payload = {
        "schema": SEMANTIC_PAYLOAD_SCHEMA, "closure_lock": lock,
        "closure_lock_sha256": canonical_sha256(lock),
        "ndf_impact": {"sha256": impact_sha256, "affected_pto_ids": affected},
        "obligations": {"selected": obligations, "completed": obligations, "passed": obligations, "failed": []},
        "cases": {
            "selected": selected_cases, "completed": selected_cases, "passed": selected_cases,
            "failed": [], "skipped": [], "timeout": [], "unknown": [],
        },
        "case_manifests": case_digests,
    }
    validate_semantic_payload(payload)
    write_canonical_json(output / "closure-semantic-payload.json", payload)
    artifact_sha256 = canonical_sha256({
        "closure-lock.json": _sha256(output / "closure-lock.json"),
        "closure-semantic-payload.json": _sha256(output / "closure-semantic-payload.json"),
        **{f"cases/{item['case_id']}/case-manifest.json": item["sha256"] for item in case_digests},
    })
    envelope = {
        "schema": RUN_ENVELOPE_SCHEMA,
        "semantic_payload_sha256": canonical_sha256(payload),
        "workflow": {
            "repository": arguments.workflow_repository,
            "path": arguments.workflow_path,
            "commit": arguments.workflow_commit,
        },
        "run": {"id": arguments.run_id, "attempt": arguments.run_attempt, "timestamp": arguments.timestamp},
        "runner": {"image": arguments.runner_image, "builder_identity": arguments.builder_identity},
        "artifact_sha256": artifact_sha256, "attestation": None,
    }
    validate_run_envelope(envelope, payload)
    write_canonical_json(output / "closure-run-envelope.json", envelope)
    return payload, envelope


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--request", required=True, type=pathlib.Path)
    parser.add_argument("--ndf-impact", required=True, type=pathlib.Path)
    parser.add_argument("--ndf-root", required=True, type=pathlib.Path)
    parser.add_argument("--aslref-root", required=True, type=pathlib.Path)
    parser.add_argument("--asl-spec", required=True, type=pathlib.Path)
    parser.add_argument("--clang", required=True, type=pathlib.Path)
    parser.add_argument("--llvm-mc", required=True, type=pathlib.Path)
    parser.add_argument("--ld-lld", required=True, type=pathlib.Path)
    parser.add_argument("--aslref", required=True, type=pathlib.Path)
    parser.add_argument("--target", default="linx64-unknown-none-elf")
    parser.add_argument("--backend", choices=("host-sparse", "reference-array"), default="host-sparse")
    parser.add_argument("--output", required=True, type=pathlib.Path)
    parser.add_argument("--workflow-repository", required=True)
    parser.add_argument("--workflow-path", required=True)
    parser.add_argument("--workflow-commit", required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--run-attempt", type=int, required=True)
    parser.add_argument(
        "--timestamp",
        default=datetime.datetime.now(datetime.timezone.utc)
        .replace(microsecond=0).isoformat().replace("+00:00", "Z"),
    )
    parser.add_argument("--runner-image", required=True)
    parser.add_argument("--builder-identity", required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    arguments = _parser().parse_args(argv)
    try:
        payload, _ = run_closure(arguments)
    except (OSError, ValueError, RuntimeError, AssertionError, subprocess.TimeoutExpired) as error:
        print(f"pto-closure: {error}", file=sys.stderr)
        return 1
    print(canonical_sha256(payload))
    return 0


__all__ = ["main", "run_closure"]
