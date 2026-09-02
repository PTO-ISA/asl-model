import copy
import json
import math
import re
import unittest
from pathlib import Path

from pto_asl_model.state import (
    ArchitectureState,
    FaultState,
    MemoryRegion,
    MemoryState,
    ScalarState,
    SharedState,
    SharedTile,
    StateEnvelope,
    TileDescriptor,
    TileState,
    TileValue,
    canonical_hash,
    canonical_json,
    state_diff,
)


ROOT = Path(__file__).resolve().parents[1]


def resolve_local_ref(root, reference):
    if not isinstance(reference, str) or not reference.startswith("#/"):
        raise AssertionError(f"unsupported schema reference: {reference!r}")
    value = root
    for token in reference[2:].split("/"):
        token = token.replace("~1", "/").replace("~0", "~")
        if not isinstance(value, dict) or token not in value:
            raise AssertionError(f"unresolved schema reference: {reference}")
        value = value[token]
    return value


def validate_instance(instance, schema, root):
    if "$ref" in schema:
        validate_instance(instance, resolve_local_ref(root, schema["$ref"]), root)
    if "const" in schema:
        assert instance == schema["const"]
    if "enum" in schema:
        assert instance in schema["enum"]
    expected = schema.get("type")
    if expected is not None:
        choices = expected if isinstance(expected, list) else [expected]
        matches = {
            "object": lambda value: isinstance(value, dict),
            "array": lambda value: isinstance(value, list),
            "string": lambda value: isinstance(value, str),
            "integer": lambda value: isinstance(value, int) and not isinstance(value, bool),
            "boolean": lambda value: isinstance(value, bool),
            "null": lambda value: value is None,
        }
        assert any(matches[choice](instance) for choice in choices)
    if isinstance(instance, int) and not isinstance(instance, bool):
        if "minimum" in schema:
            assert instance >= schema["minimum"]
        if "maximum" in schema:
            assert instance <= schema["maximum"]
    if isinstance(instance, str) and "pattern" in schema:
        assert re.fullmatch(schema["pattern"], instance)
    if isinstance(instance, dict):
        required = schema.get("required", [])
        assert all(key in instance for key in required)
        properties = schema.get("properties", {})
        for key, value in instance.items():
            if isinstance(schema.get("propertyNames"), dict):
                validate_instance(key, schema["propertyNames"], root)
            if key in properties:
                validate_instance(value, properties[key], root)
            elif schema.get("additionalProperties") is False:
                raise AssertionError(f"unexpected property: {key}")
            elif isinstance(schema.get("additionalProperties"), dict):
                validate_instance(value, schema["additionalProperties"], root)
    if isinstance(instance, list) and isinstance(schema.get("items"), dict):
        for value in instance:
            validate_instance(value, schema["items"], root)


class AslStateSchemaTest(unittest.TestCase):
    def test_schema_is_versioned_and_covers_architecture_domains(self):
        schema = json.loads(
            (ROOT / "src" / "pto_asl_model" / "architecture_state.schema.json").read_text(encoding="utf-8")
        )
        self.assertEqual(schema["$schema"], "https://json-schema.org/draft/2020-12/schema")
        self.assertEqual(schema["$defs"]["state"]["required"], [
            "scalar", "block", "tile", "shared", "memory", "fault",
            "pe_id", "thread_id", "cycle", "extensions",
        ])
        for domain in ("scalar", "block", "tile", "shared", "memory", "fault"):
            self.assertIn(domain, schema["$defs"])

    def test_all_local_refs_resolve_and_remaining_envelopes_validate(self):
        schema = json.loads(
            (ROOT / "src" / "pto_asl_model" / "architecture_state.schema.json").read_text(
                encoding="utf-8"
            )
        )

        def walk(value):
            if isinstance(value, dict):
                if "$ref" in value:
                    self.assertIsInstance(resolve_local_ref(schema, value["$ref"]), dict)
                for child in value.values():
                    walk(child)
            elif isinstance(value, list):
                for child in value:
                    walk(child)

        walk(schema)
        for envelope in (
            StateEnvelope.initial(ArchitectureState()),
            StateEnvelope(kind="state_snapshot", state=ArchitectureState()),
            StateEnvelope.initial(ArchitectureState(
                tile=TileState(registers={"t0": TileValue()}),
                shared=SharedState(tiles={"s0": SharedTile()}, generations={"s0": 0}),
                memory=MemoryState(
                    cells={"0x1000": 1},
                    regions=[MemoryRegion(0x1000, 4, "rw", "data")],
                ),
            )),
        ):
            validate_instance(envelope.as_dict(), schema, schema)

    def test_canonical_serialization_is_order_independent(self):
        left = {"z": [2, 1], "a": {"b": 2, "a": 1}}
        right = {"a": {"a": 1, "b": 2}, "z": [2, 1]}
        self.assertEqual(canonical_json(left), canonical_json(right))
        self.assertEqual(canonical_hash(left), canonical_hash(right))
        with self.assertRaises(ValueError):
            canonical_json({"bad": math.nan})

    def test_non_text_mapping_keys_are_rejected_without_hash_aliasing(self):
        collision = {1: "integer-key", "1": "text-key"}
        for operation in (canonical_json, canonical_hash):
            with self.subTest(operation=operation.__name__):
                with self.assertRaisesRegex(ValueError, "mapping keys must be text"):
                    operation(collision)
        for label, construct in (
            (
                "metadata",
                lambda: StateEnvelope.initial(
                    ArchitectureState(), metadata={"nested": collision}
                ),
            ),
            (
                "artifact",
                lambda: StateEnvelope.initial(
                    ArchitectureState(), artifact={"nested": collision}
                ),
            ),
            (
                "extensions",
                lambda: ArchitectureState(extensions={"nested": collision}),
            ),
            ("tile data", lambda: TileValue(data=[collision])),
        ):
            with self.subTest(channel=label):
                with self.assertRaisesRegex(ValueError, "mapping keys must be text"):
                    construct()

    def test_memory_schema_matches_canonical_python_forms(self):
        schema = json.loads(
            (ROOT / "src" / "pto_asl_model" / "architecture_state.schema.json").read_text(
                encoding="utf-8"
            )
        )
        permissions = schema["$defs"]["region"]["properties"]["permissions"]
        self.assertEqual(
            permissions["enum"], ["r", "w", "x", "rw", "rx", "wx", "rwx"]
        )
        property_names = schema["$defs"]["memory"]["properties"]["cells"][
            "propertyNames"
        ]
        self.assertEqual(property_names["pattern"], "^0x(?:0|[1-9a-f][0-9a-f]*)$")

        valid = StateEnvelope.initial(
            ArchitectureState(
                memory=MemoryState(
                    cells={"0x0": 0, "0x10": 255},
                    regions=[MemoryRegion(0, 16, "rwx")],
                )
            )
        ).as_dict()
        validate_instance(valid, schema, schema)

        for invalid in ("", "rr", "wr", "xrw", None, ["r"]):
            with self.subTest(permission=invalid):
                payload = copy.deepcopy(valid)
                payload["state"]["memory"]["regions"][0]["permissions"] = invalid
                with self.assertRaises(AssertionError):
                    validate_instance(payload, schema, schema)
                with self.assertRaises(ValueError):
                    MemoryRegion(0, 1, invalid)

        for invalid in ("0", "1", "01", "0x00", "0x01", "0X1", "-1", "0xg"):
            with self.subTest(address=invalid):
                payload = copy.deepcopy(valid)
                payload["state"]["memory"]["cells"] = {invalid: 1}
                with self.assertRaises(AssertionError):
                    validate_instance(payload, schema, schema)
                with self.assertRaises(ValueError):
                    MemoryState(cells={invalid: 1})

        for invalid in (-1, 256, True):
            with self.subTest(byte=invalid):
                payload = copy.deepcopy(valid)
                payload["state"]["memory"]["cells"] = {"0x0": invalid}
                with self.assertRaises(AssertionError):
                    validate_instance(payload, schema, schema)
                with self.assertRaises(ValueError):
                    MemoryState(cells={"0x0": invalid})

    def test_state_round_trip_preserves_tile_memory_and_extensions(self):
        state = ArchitectureState(
            scalar=ScalarState(registers={"x1": 7}, pc=0x100, tpc=0x200),
            tile=TileState(
                registers={
                    "t0": TileValue(
                        descriptor=TileDescriptor(
                            dtype="fp16", layout="row_major", shape=[2, 2],
                            valid_shape=[1, 2], strides=[2, 1],
                        ),
                        data=[1, 2, 0, 0], defined=[True, True, False, False],
                        generation=3,
                    )
                }
            ),
            memory=MemoryState(
                cells={"0x1000": 42}, regions=[MemoryRegion(0x1000, 16, "rw", "input")]
            ),
            fault=FaultState(),
            extensions={"future.domain": {"version": 2}},
        )
        restored = ArchitectureState.from_dict(state.as_dict())
        self.assertEqual(restored.as_dict(), state.as_dict())
        self.assertEqual(restored.sha256(), state.sha256())

    def test_state_envelopes_are_deterministic(self):
        initial = StateEnvelope.initial(ArchitectureState(), artifact={"commit": "abc"})
        payload = initial.as_dict()
        self.assertEqual(payload["schema"], "pto.asl-model.arch-state.v1")
        self.assertEqual(payload["kind"], "initial_state")
        self.assertEqual(initial.sha256(), canonical_hash(payload))
        restored = StateEnvelope.from_dict(payload)
        self.assertEqual(restored.as_dict(), payload)

    def test_nested_dto_constructors_reject_schema_type_violations(self):
        with self.assertRaisesRegex(ValueError, "scalar.flags"):
            ScalarState(flags={"bad": "true"})
        with self.assertRaisesRegex(ValueError, "scalar.flags"):
            ScalarState(flags={"bad": {}})
        with self.assertRaisesRegex(ValueError, "fault.pending"):
            FaultState(pending=1)
        with self.assertRaisesRegex(ValueError, "tile value generation"):
            TileValue(generation=-1)

    def test_nested_state_round_trip_rejects_schema_violations(self):
        state = ArchitectureState(
            tile=TileState(registers={"t0": TileValue()}),
            shared=SharedState(
                tiles={"s0": SharedTile()}, generations={"s0": 0}
            ),
            memory=MemoryState(
                cells={"0x1000": 1},
                regions=[MemoryRegion(0x1000, 4, "rw", "data")],
            ),
        )
        base = StateEnvelope.initial(state).as_dict()
        cases = (
            (("state", "scalar", "flags"), {"bad": "yes"}, "scalar.flags"),
            (("state", "scalar", "flags"), {"bad": {}}, "scalar.flags"),
            (("state", "scalar", "mode"), 7, "scalar.mode"),
            (("state", "block", "active"), 1, "block.active"),
            (("state", "block", "block_id"), True, "block.block_id"),
            (("state", "block", "instruction_count"), -1, "block.instruction_count"),
            (("state", "tile", "registers"), [], "tile registers"),
            (("state", "tile", "registers", "t0", "descriptor", "dtype"), [], "dtype"),
            (("state", "tile", "registers", "t0", "descriptor", "shape"), [-1], "shape"),
            (("state", "tile", "registers", "t0", "descriptor", "strides"), [True], "strides"),
            (("state", "tile", "registers", "t0", "data"), {}, "data"),
            (("state", "tile", "registers", "t0", "defined"), [1], "defined"),
            (("state", "tile", "registers", "t0", "generation"), -1, "generation"),
            (("state", "shared", "tiles", "s0", "generation"), -1, "generation"),
            (("state", "shared", "tiles", "s0", "allocation_mask"), -1, "allocation_mask"),
            (("state", "shared", "tiles"), [], "shared tiles"),
            (("state", "shared", "generations", "s0"), -1, "shared.generations"),
            (("state", "memory", "cells", "0x1000"), 999, "memory cells"),
            (("state", "memory", "regions"), {}, "memory regions"),
            (("state", "fault", "pending"), 1, "fault.pending"),
            (("state", "fault", "address"), True, "fault.address"),
            (("state", "fault", "message"), {}, "fault.message"),
            (("state", "fault", "kind"), [], "fault.kind"),
            (("state", "fault", "recoverable"), 0, "fault.recoverable"),
            (("state", "extensions"), [], "extensions"),
        )
        for path, replacement, message in cases:
            with self.subTest(path=path):
                payload = copy.deepcopy(base)
                target = payload
                for key in path[:-1]:
                    target = target[key]
                target[path[-1]] = replacement
                with self.assertRaisesRegex((TypeError, ValueError), message):
                    StateEnvelope.from_dict(payload)

    def test_additional_properties_are_rejected_at_every_closed_dto(self):
        state = ArchitectureState(
            tile=TileState(registers={"t0": TileValue()}),
            shared=SharedState(tiles={"s0": SharedTile()}, generations={"s0": 0}),
            memory=MemoryState(
                cells={"0x1000": 1},
                regions=[MemoryRegion(0x1000, 4, "rw", "data")],
            ),
        )
        base = StateEnvelope.initial(state).as_dict()
        closed_paths = (
            (),
            ("state",),
            ("state", "scalar"),
            ("state", "block"),
            ("state", "tile"),
            ("state", "tile", "registers", "t0"),
            ("state", "tile", "registers", "t0", "descriptor"),
            ("state", "shared"),
            ("state", "shared", "tiles", "s0"),
            ("state", "memory"),
            ("state", "memory", "regions", 0),
            ("state", "fault"),
        )
        for path in closed_paths:
            with self.subTest(path=path):
                payload = copy.deepcopy(base)
                target = payload
                for key in path:
                    target = target[key]
                target["unexpected"] = 1
                with self.assertRaisesRegex(ValueError, "unexpected fields: unexpected"):
                    StateEnvelope.from_dict(payload)

    def test_extensions_are_the_positive_additive_round_trip_channel(self):
        descriptor = TileDescriptor(extensions={"descriptor.future": {"v": 1}})
        state = ArchitectureState(
            scalar=ScalarState(extensions={"scalar.future": True}),
            tile=TileState(
                registers={
                    "t0": TileValue(
                        descriptor=descriptor,
                        extensions={"tile_value.future": [1, 2]},
                    )
                },
                extensions={"tile.future": None},
            ),
            shared=SharedState(
                tiles={
                    "s0": SharedTile(
                        descriptor=descriptor,
                        extensions={"shared_tile.future": "ok"},
                    )
                },
                extensions={"shared.future": 3},
            ),
            memory=MemoryState(
                regions=[
                    MemoryRegion(
                        0x1000,
                        4,
                        "rw",
                        "data",
                        extensions={"region.future": False},
                    )
                ],
                extensions={"memory.future": {}},
            ),
            fault=FaultState(extensions={"fault.future": 1.5}),
            extensions={"state.future": {"enabled": True}},
        )
        envelope = StateEnvelope.initial(state)
        restored = StateEnvelope.from_dict(envelope.as_dict())
        self.assertEqual(restored.as_dict(), envelope.as_dict())

    def test_state_diff_only_reports_changed_domains(self):
        before = ArchitectureState()
        after = ArchitectureState(scalar=ScalarState(pc=4))
        diff = state_diff(before, after)
        self.assertEqual(set(diff), {"scalar"})
        self.assertEqual(diff["scalar"]["before"]["pc"], 0)
        self.assertEqual(diff["scalar"]["after"]["pc"], 4)


if __name__ == "__main__":
    unittest.main()
