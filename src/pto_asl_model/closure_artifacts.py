"""Canonical closure artifacts and fail-closed structural validation."""

from __future__ import annotations

import hashlib
import json
import pathlib
import re
from collections.abc import Mapping


REQUEST_SCHEMA = "pto-closure-request-v1"
LOCK_SCHEMA = "pto-closure-lock-v1"
SEMANTIC_PAYLOAD_SCHEMA = "pto-closure-semantic-payload-v1"
RUN_ENVELOPE_SCHEMA = "pto-closure-run-envelope-v1"
CASE_SCHEMA = "pto-avs-case-v1"

RELEASE = "0.58.6"
PUBLICATION_VERSION = "0.58.6.0"
ENCODING_ABI = "pto-isa-0.58.6-mode-function-v1"
ENCODING_PROJECTION_SHA256 = (
    "a757f2e50ec8050d2131b6b9ad38657511df80cf3f9424d5f009ea6e0cc35839"
)
NDF_COMMIT = "ed356980ce7ecb2e8482902988d5012fb54058b3"
ASLREF_COMMIT = "5873cbb69312d92b4b97131cff840ec621b12ddf"

SHA256_RE = re.compile(r"[0-9a-f]{64}\Z")
GIT_ID_RE = re.compile(r"[0-9a-f]{40}\Z")
REPOSITORY_RE = re.compile(r"https://github\.com/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+\.git\Z")
HOSTED_REPOSITORY_RE = re.compile(
    r"https://github\.com/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+\Z"
)
CASE_ID_RE = re.compile(r"[a-z0-9]+(?:[._-][a-z0-9]+)*\Z")
TIMESTAMP_RE = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z\Z")


def canonical_json_bytes(value: object) -> bytes:
    """Return the one byte representation used by closure digests."""
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
    ).encode("utf-8")


def canonical_sha256(value: object) -> str:
    return hashlib.sha256(canonical_json_bytes(value)).hexdigest()


def canonical_repository_url(repository: str) -> str:
    """Normalize a GitHub Actions checkout origin to the lock URL form."""
    if REPOSITORY_RE.fullmatch(repository) is not None:
        return repository
    if HOSTED_REPOSITORY_RE.fullmatch(repository) is not None:
        return repository + ".git"
    raise ValueError("repository origin is not a canonical GitHub HTTPS URL")


def write_canonical_json(path: pathlib.Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(canonical_json_bytes(value) + b"\n")


def read_json(path: pathlib.Path) -> dict[str, object]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path}: root must be an object")
    return value


def _object(value: object, label: str) -> dict[str, object]:
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be an object")
    return value


def _exact_fields(value: Mapping[str, object], fields: set[str], label: str) -> None:
    actual = set(value)
    missing = sorted(fields - actual)
    extra = sorted(actual - fields)
    if missing or extra:
        details = []
        if missing:
            details.append("missing " + ", ".join(missing))
        if extra:
            details.append("unknown " + ", ".join(extra))
        raise ValueError(f"{label} fields invalid: {'; '.join(details)}")


def _text(value: object, label: str) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{label} must be non-empty text")
    return value


def _string_list(value: object, label: str, *, nonempty: bool = False) -> list[str]:
    if not isinstance(value, list) or any(not isinstance(item, str) or not item for item in value):
        raise ValueError(f"{label} must be a string array")
    if value != sorted(set(value)):
        raise ValueError(f"{label} must be sorted and unique")
    if nonempty and not value:
        raise ValueError(f"{label} must not be empty")
    return value


def _arguments(value: object, label: str) -> list[str]:
    if not isinstance(value, list) or any(not isinstance(item, str) or not item for item in value):
        raise ValueError(f"{label} must be a non-empty-string array")
    return value


def _digest(value: object, label: str) -> str:
    text = _text(value, label)
    if SHA256_RE.fullmatch(text) is None:
        raise ValueError(f"{label} must be a lowercase SHA-256")
    return text


def _git_id(value: object, label: str) -> str:
    text = _text(value, label)
    if GIT_ID_RE.fullmatch(text) is None:
        raise ValueError(f"{label} must be a full lowercase git object ID")
    return text


def _repository_identity(value: object, label: str) -> dict[str, object]:
    item = _object(value, label)
    _exact_fields(item, {"repository", "commit", "tree"}, label)
    repository = _text(item["repository"], f"{label}.repository")
    if REPOSITORY_RE.fullmatch(repository) is None:
        raise ValueError(f"{label}.repository must be a canonical GitHub HTTPS URL ending in .git")
    _git_id(item["commit"], f"{label}.commit")
    _git_id(item["tree"], f"{label}.tree")
    return item


def validate_identity(value: object, label: str = "identity") -> dict[str, object]:
    identity = _object(value, label)
    _exact_fields(
        identity,
        {"release", "publication_version", "encoding_abi", "encoding_projection_sha256"},
        label,
    )
    expected = {
        "release": RELEASE,
        "publication_version": PUBLICATION_VERSION,
        "encoding_abi": ENCODING_ABI,
        "encoding_projection_sha256": ENCODING_PROJECTION_SHA256,
    }
    if identity != expected:
        raise ValueError(f"{label} does not equal the PTO {RELEASE} release identity")
    return identity


def validate_request(value: object) -> dict[str, object]:
    request = _object(value, "closure request")
    _exact_fields(request, {"schema", "identity", "repositories", "policy"}, "closure request")
    if request["schema"] != REQUEST_SCHEMA:
        raise ValueError("closure request schema mismatch")
    validate_identity(request["identity"])
    repositories = _object(request["repositories"], "repositories")
    _exact_fields(repositories, {"pto_spec", "llvm", "asl_model"}, "repositories")
    for name, item in repositories.items():
        candidate = _object(item, f"repositories.{name}")
        _exact_fields(candidate, {"repository", "commit", "path"}, f"repositories.{name}")
        repository = _text(candidate["repository"], f"repositories.{name}.repository")
        if REPOSITORY_RE.fullmatch(repository) is None:
            raise ValueError(f"repositories.{name}.repository is not canonical")
        _git_id(candidate["commit"], f"repositories.{name}.commit")
        _text(candidate["path"], f"repositories.{name}.path")
    policy = _object(request["policy"], "policy")
    _exact_fields(policy, {"affected_pto_ids", "mandatory_case_ids"}, "policy")
    _string_list(policy["affected_pto_ids"], "policy.affected_pto_ids")
    _string_list(policy["mandatory_case_ids"], "policy.mandatory_case_ids")
    return request


def validate_lock(value: object) -> dict[str, object]:
    lock = _object(value, "closure lock")
    _exact_fields(
        lock,
        {"schema", "identity", "repositories", "tools", "target", "model", "corpus", "obligations"},
        "closure lock",
    )
    if lock["schema"] != LOCK_SCHEMA:
        raise ValueError("closure lock schema mismatch")
    validate_identity(lock["identity"])
    repositories = _object(lock["repositories"], "repositories")
    _exact_fields(
        repositories,
        {"pto_spec", "llvm", "asl_model", "normative_language", "aslref"},
        "repositories",
    )
    for name, item in repositories.items():
        identity = _repository_identity(item, f"repositories.{name}")
        if name == "normative_language" and identity["commit"] != NDF_COMMIT:
            raise ValueError("normative_language commit mismatch")
        if name == "aslref" and identity["commit"] != ASLREF_COMMIT:
            raise ValueError("ASLRef commit mismatch")
    tools = _object(lock["tools"], "tools")
    _exact_fields(tools, {"clang", "llvm_mc", "ld_lld", "aslref"}, "tools")
    for name, item in tools.items():
        tool = _object(item, f"tools.{name}")
        _exact_fields(tool, {"path", "sha256"}, f"tools.{name}")
        _text(tool["path"], f"tools.{name}.path")
        _digest(tool["sha256"], f"tools.{name}.sha256")
    target = _object(lock["target"], "target")
    _exact_fields(target, {"triple"}, "target")
    _text(target["triple"], "target.triple")
    model = _object(lock["model"], "model")
    _exact_fields(model, {"abi", "worker_protocol", "backend", "profile"}, "model")
    for field in model:
        _text(model[field], f"model.{field}")
    if model["abi"] != "pto-asl-model-experimental-v2":
        raise ValueError("model ABI mismatch")
    if model["worker_protocol"] != "pto-asl-worker-v1":
        raise ValueError("worker protocol mismatch")
    if model["backend"] not in {"host-sparse", "reference-array"}:
        raise ValueError("model backend mismatch")
    if model["profile"] != "bounded-reference-v1":
        raise ValueError("model profile mismatch")
    corpus = _object(lock["corpus"], "corpus")
    _exact_fields(corpus, {"sha256", "case_ids"}, "corpus")
    _digest(corpus["sha256"], "corpus.sha256")
    _string_list(corpus["case_ids"], "corpus.case_ids", nonempty=True)
    obligations = _object(lock["obligations"], "obligations")
    _exact_fields(obligations, {"affected_pto_ids", "selected", "sha256"}, "obligations")
    _string_list(obligations["affected_pto_ids"], "obligations.affected_pto_ids")
    selected = _string_list(obligations["selected"], "obligations.selected", nonempty=True)
    _digest(obligations["sha256"], "obligations.sha256")
    if obligations["sha256"] != canonical_sha256(selected):
        raise ValueError("obligations.sha256 mismatch")
    return lock


def validate_semantic_payload(value: object) -> dict[str, object]:
    payload = _object(value, "semantic payload")
    _exact_fields(
        payload,
        {"schema", "closure_lock", "closure_lock_sha256", "ndf_impact", "obligations", "cases", "case_manifests"},
        "semantic payload",
    )
    if payload["schema"] != SEMANTIC_PAYLOAD_SCHEMA:
        raise ValueError("semantic payload schema mismatch")
    lock = validate_lock(payload["closure_lock"])
    if _digest(payload["closure_lock_sha256"], "closure_lock_sha256") != canonical_sha256(lock):
        raise ValueError("closure_lock_sha256 mismatch")
    impact = _object(payload["ndf_impact"], "ndf_impact")
    _exact_fields(impact, {"sha256", "affected_pto_ids"}, "ndf_impact")
    _digest(impact["sha256"], "ndf_impact.sha256")
    affected = _string_list(impact["affected_pto_ids"], "ndf_impact.affected_pto_ids")
    if affected != lock["obligations"]["affected_pto_ids"]:  # type: ignore[index]
        raise ValueError("NDF affected PTO IDs do not match closure lock")
    obligations = _object(payload["obligations"], "obligations")
    _exact_fields(obligations, {"selected", "completed", "passed", "failed"}, "obligations")
    obligation_sets = {name: _string_list(value, f"obligations.{name}") for name, value in obligations.items()}
    if obligation_sets["selected"] != lock["obligations"]["selected"]:  # type: ignore[index]
        raise ValueError("selected obligations do not match closure lock")
    if not (
        obligation_sets["selected"]
        == obligation_sets["completed"]
        == obligation_sets["passed"]
    ) or obligation_sets["failed"]:
        raise ValueError("closure obligations are not completely passing")
    cases = _object(payload["cases"], "cases")
    names = {"selected", "completed", "passed", "failed", "skipped", "timeout", "unknown"}
    _exact_fields(cases, names, "cases")
    case_sets = {name: _string_list(cases[name], f"cases.{name}") for name in names}
    if case_sets["selected"] != lock["corpus"]["case_ids"]:  # type: ignore[index]
        raise ValueError("selected cases do not match closure lock")
    if not (
        case_sets["selected"]
        == case_sets["completed"]
        == case_sets["passed"]
    ) or any(case_sets[name] for name in ("failed", "skipped", "timeout", "unknown")):
        raise ValueError("closure cases are not completely passing")
    manifests = payload["case_manifests"]
    if not isinstance(manifests, list):
        raise ValueError("case_manifests must be an array")
    normalized: list[tuple[str, str]] = []
    for index, value in enumerate(manifests):
        item = _object(value, f"case_manifests[{index}]")
        _exact_fields(item, {"case_id", "sha256"}, f"case_manifests[{index}]")
        normalized.append((_text(item["case_id"], "case_id"), _digest(item["sha256"], "sha256")))
    if normalized != sorted(set(normalized)):
        raise ValueError("case_manifests must be sorted and unique")
    if [case_id for case_id, _ in normalized] != case_sets["completed"]:
        raise ValueError("case manifests do not equal completed cases")
    return payload


def validate_run_envelope(value: object, semantic_payload: object) -> dict[str, object]:
    envelope = _object(value, "run envelope")
    _exact_fields(
        envelope,
        {"schema", "semantic_payload_sha256", "workflow", "run", "runner", "artifact_sha256", "attestation"},
        "run envelope",
    )
    if envelope["schema"] != RUN_ENVELOPE_SCHEMA:
        raise ValueError("run envelope schema mismatch")
    validate_semantic_payload(semantic_payload)
    expected = canonical_sha256(semantic_payload)
    if _digest(envelope["semantic_payload_sha256"], "semantic_payload_sha256") != expected:
        raise ValueError("semantic payload digest mismatch")
    workflow = _object(envelope["workflow"], "workflow")
    _exact_fields(workflow, {"repository", "path", "commit"}, "workflow")
    _text(workflow["repository"], "workflow.repository")
    _text(workflow["path"], "workflow.path")
    _git_id(workflow["commit"], "workflow.commit")
    run = _object(envelope["run"], "run")
    _exact_fields(run, {"id", "attempt", "timestamp"}, "run")
    _text(run["id"], "run.id")
    if not isinstance(run["attempt"], int) or isinstance(run["attempt"], bool) or run["attempt"] < 1:
        raise ValueError("run.attempt must be a positive integer")
    timestamp = _text(run["timestamp"], "run.timestamp")
    if TIMESTAMP_RE.fullmatch(timestamp) is None:
        raise ValueError("run.timestamp must be whole-second UTC ending in Z")
    runner = _object(envelope["runner"], "runner")
    _exact_fields(runner, {"image", "builder_identity"}, "runner")
    _text(runner["image"], "runner.image")
    _text(runner["builder_identity"], "runner.builder_identity")
    _digest(envelope["artifact_sha256"], "artifact_sha256")
    if envelope["attestation"] is not None and not isinstance(envelope["attestation"], dict):
        raise ValueError("attestation must be an object or null")
    return envelope


def validate_case(value: object) -> dict[str, object]:
    case = _object(value, "AVS case")
    fields = {
        "schema", "case_id", "lane", "source", "linker_script", "pto_ids", "obligation_ids",
        "features", "avs_ids", "expected_length_sequence", "compile", "link", "execution", "model", "golden",
        "profile", "resource_class", "timeout_seconds",
    }
    _exact_fields(case, fields, "AVS case")
    if case["schema"] != CASE_SCHEMA:
        raise ValueError("AVS case schema mismatch")
    case_id = _text(case["case_id"], "case_id")
    if CASE_ID_RE.fullmatch(case_id) is None:
        raise ValueError("case_id is not stable lowercase identifier syntax")
    if case["lane"] not in {"c", "cxx", "ir", "intrinsic", "asm"}:
        raise ValueError("unsupported AVS lane")
    _text(case["source"], "source")
    _text(case["linker_script"], "linker_script")
    _string_list(case["pto_ids"], "pto_ids", nonempty=True)
    _string_list(case["obligation_ids"], "obligation_ids", nonempty=True)
    _string_list(case["features"], "features")
    _string_list(case["avs_ids"], "avs_ids")
    lengths = case["expected_length_sequence"]
    if not isinstance(lengths, list) or any(length not in {16, 32, 48, 64} for length in lengths):
        raise ValueError("expected_length_sequence contains an unsupported instruction length")
    for field in ("compile", "link"):
        command = _object(case[field], field)
        _exact_fields(command, {"tool", "arguments"}, field)
        if command["tool"] not in {"clang", "llvm_mc", "ld_lld"}:
            raise ValueError(f"unsupported {field} tool")
        _arguments(command["arguments"], f"{field}.arguments")
    execution = _object(case["execution"], "execution")
    _exact_fields(
        execution,
        {"start_acr", "start_symbol", "return_symbol", "stop_symbol", "result_symbol", "result_size_symbol", "stop_after_hits", "max_steps", "stack_top", "host_request"},
        "execution",
    )
    for field in ("start_symbol", "return_symbol", "stop_symbol", "result_symbol", "result_size_symbol"):
        _text(execution[field], f"execution.{field}")
    for field in ("stop_after_hits", "max_steps", "stack_top"):
        if not isinstance(execution[field], int) or isinstance(execution[field], bool) or execution[field] < 1:
            raise ValueError(f"execution.{field} must be positive")
    if (
        not isinstance(execution["start_acr"], int)
        or isinstance(execution["start_acr"], bool)
        or execution["start_acr"] < 0
        or execution["start_acr"] > 15
    ):
        raise ValueError("execution.start_acr must be an integer from 0 through 15")
    host_request = execution["host_request"]
    if host_request is not None:
        request = _object(host_request, "execution.host_request")
        _exact_fields(request, {"number", "argument0", "service_request_type"}, "execution.host_request")
        for field in request:
            if not isinstance(request[field], int) or isinstance(request[field], bool) or request[field] < 0:
                raise ValueError(f"execution.host_request.{field} must be non-negative")
    model = _object(case["model"], "model")
    _exact_fields(model, {"memory_bytes", "tile_elements", "runtime_typecheck", "backend"}, "model")
    if model["runtime_typecheck"] not in {"strict", "minimal"}:
        raise ValueError("unsupported runtime typecheck mode")
    if model["backend"] not in {"reference-array", "host-sparse"}:
        raise ValueError("unsupported memory backend")
    for field in ("memory_bytes", "tile_elements"):
        if not isinstance(model[field], int) or isinstance(model[field], bool) or model[field] < 1:
            raise ValueError(f"model.{field} must be positive")
    golden = _object(case["golden"], "golden")
    _exact_fields(
        golden,
        {"policy", "encoding", "path", "sha256", "provenance"},
        "golden",
    )
    if golden["policy"] != "exact":
        raise ValueError("initial closure supports only exact independent golden data")
    if golden["encoding"] not in {"raw", "hex"}:
        raise ValueError("unsupported golden encoding")
    _text(golden["path"], "golden.path")
    _digest(golden["sha256"], "golden.sha256")
    provenance = _object(golden["provenance"], "golden.provenance")
    _exact_fields(
        provenance,
        {"source", "rationale", "model_generated"},
        "golden.provenance",
    )
    _text(provenance["source"], "golden.provenance.source")
    _text(provenance["rationale"], "golden.provenance.rationale")
    if provenance["model_generated"] is not False:
        raise ValueError("golden provenance must state model_generated=false")
    _text(case["profile"], "profile")
    _text(case["resource_class"], "resource_class")
    if not isinstance(case["timeout_seconds"], int) or isinstance(case["timeout_seconds"], bool) or case["timeout_seconds"] < 1:
        raise ValueError("timeout_seconds must be positive")
    return case


__all__ = [
    "ASLREF_COMMIT", "CASE_SCHEMA", "ENCODING_ABI",
    "ENCODING_PROJECTION_SHA256", "LOCK_SCHEMA", "NDF_COMMIT",
    "PUBLICATION_VERSION", "RELEASE", "REQUEST_SCHEMA",
    "RUN_ENVELOPE_SCHEMA", "SEMANTIC_PAYLOAD_SCHEMA", "canonical_json_bytes",
    "canonical_repository_url", "canonical_sha256", "read_json", "validate_case", "validate_identity",
    "validate_lock", "validate_request", "validate_run_envelope",
    "validate_semantic_payload", "write_canonical_json",
]
