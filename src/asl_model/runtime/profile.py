"""Explicit ASL model-profile selection and materialization."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from pathlib import Path

from ..paths import cache_root as resolve_cache_root


class ProfileError(ValueError):
    """Raised when a generated ASL profile cannot be selected safely."""


@dataclass(frozen=True)
class AslModelProfile:
    """Compile-time ASL switches selected by the runtime, never guessed."""

    name: str
    switches: tuple[tuple[str, str], ...]

    _KNOWN = {
        "portable": (),
        "linx-runtime": (
            ("PTO_MODEL_LINX_RUNTIME_COMPAT", "TRUE"),
            ("PTO_MODEL_ALLOW_SYSTEM_OPS_IN_NON_SYS_BLOCK", "TRUE"),
            ("PTO_MODEL_HOST_MEMORY", "TRUE"),
            ("PTO_MODEL_FRAME_SP_INDEX", "1"),
            # The legacy stop alias is restricted in ASL to call-boundary
            # completion, so ordinary compressed BSTART.STD transitions are
            # not reinterpreted.
            ("PTO_MODEL_LINX_LEGACY_C_BSTOP", "TRUE"),
            ("PTO_MODEL_LINX_TRACE_BOUNDARY_COMPAT", "TRUE"),
            ("PTO_MODEL_MSET_MAX_BYTES", "262144"),
        ),
    }

    @classmethod
    def select(cls, name: str) -> "AslModelProfile":
        try:
            switches = cls._KNOWN[name]
        except KeyError as error:
            raise ProfileError(f"unknown ASL model profile: {name}") from error
        return cls(name, switches)

    @property
    def frame_sp_index(self) -> int:
        """Return the ABI-selected absolute GPR used as the frame SP."""

        for symbol, value in self.switches:
            if symbol == "PTO_MODEL_FRAME_SP_INDEX":
                return int(value, 0)
        return 1

    def materialize(self, pto_spec_root: Path, cache_root: Path | None = None) -> Path:
        """Return a profile-specific generated spec without changing ASL sources."""

        root = Path(pto_spec_root).resolve()
        source = root / "build" / "pto-spec.asl"
        if not source.is_file():
            raise FileNotFoundError(f"missing generated ASL artifact: {source}")
        text = materialize_profile(source.read_text(encoding="utf-8"), self.name)
        if not self.switches:
            return source
        digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
        # Keep generated profile projections beside this standalone package.
        # The ASL specification remains an explicit input; no repository
        # layout is assumed here.
        cache_root = resolve_cache_root(cache_root)
        target = cache_root.resolve() / "profiles" / self.name / f"{digest}.asl"
        target.parent.mkdir(parents=True, exist_ok=True)
        projected = (
            "// Generated profile projection; source semantics remain in pto-spec/asl.\n"
            f"// profile: {self.name}\n" + text
        )
        # A profile cache key is derived from the projected artifact, but a
        # previously interrupted write (or an older materializer) can leave a
        # same-name malformed file.  Validate the cache contents before use;
        # this keeps worker startup deterministic and fail-closed.
        if not target.is_file() or target.read_text(encoding="utf-8") != projected:
            target.write_text(projected, encoding="utf-8")
        return target


PORTABLE_PROFILE = AslModelProfile.select("portable")
LINX_RUNTIME_PROFILE = AslModelProfile.select("linx-runtime")

_CONFIG_PATTERN = re.compile(
    r"^(?P<prefix>\s*config\s+(?P<name>PTO_MODEL_[A-Z0-9_]+)\s*:\s*"
    r"[^=\n]+?\s*=\s*)(?P<value>TRUE|FALSE|-?[0-9]+)(?P<suffix>\s*;\s*)$",
    re.MULTILINE,
)


def materialize_profile(source: str, model_profile: str = "portable") -> str:
    """Apply a named profile to generated ASL text.

    The generated artifact declares each model switch exactly once. This
    helper validates that contract and applies only the profile's declared
    compile-time values; it never rewrites instruction handlers or source ASL.
    """

    if not isinstance(source, str) or not source:
        raise ProfileError("ASL model profile requires a non-empty artifact")
    profile = AslModelProfile.select(model_profile)
    matches: dict[str, re.Match[str]] = {}
    for match in _CONFIG_PATTERN.finditer(source):
        symbol = match.group("name")
        if symbol in matches:
            raise ProfileError(
                f"ASL artifact declares profile config more than once: {symbol}"
            )
        matches[symbol] = match

    known_symbols = {
        symbol for symbol, _value in AslModelProfile._KNOWN["linx-runtime"]
    }
    missing = sorted(known_symbols - set(matches))
    if missing:
        raise ProfileError(
            "ASL artifact is missing profile config(s): " + ", ".join(missing)
        )
    if profile.name == "portable":
        expected = {
            "PTO_MODEL_LINX_RUNTIME_COMPAT": "FALSE",
            "PTO_MODEL_ALLOW_SYSTEM_OPS_IN_NON_SYS_BLOCK": "FALSE",
            "PTO_MODEL_HOST_MEMORY": "FALSE",
            "PTO_MODEL_FRAME_SP_INDEX": "1",
            "PTO_MODEL_LINX_LEGACY_C_BSTOP": "FALSE",
            "PTO_MODEL_LINX_TRACE_BOUNDARY_COMPAT": "FALSE",
            "PTO_MODEL_MSET_MAX_BYTES": "262144",
        }
        for symbol, value in expected.items():
            if matches[symbol].group("value") != value:
                raise ProfileError(f"portable ASL profile requires {symbol}={value}")
        return source

    replacements = [
        (matches[symbol].start("value"), matches[symbol].end("value"), value)
        for symbol, value in profile.switches
    ]
    replacements.sort(key=lambda replacement: replacement[0], reverse=True)
    for start, end, value in replacements:
        source = source[:start] + value + source[end:]
    return source


__all__ = [
    "AslModelProfile",
    "LINX_RUNTIME_PROFILE",
    "PORTABLE_PROFILE",
    "ProfileError",
    "materialize_profile",
]
