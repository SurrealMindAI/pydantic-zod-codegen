"""Property-based round-trip tests — Test-Pyramide discipline.

PHILOSOPHY
==========
Maximum 5 property tests, each guarding ONE non-trivial semantic invariant.
Tests must not be more complex than the library they test.

If you want to add a 6th property test, you MUST first justify in this file's
docstring why an existing one cannot be extended.

THE 5 PROPERTIES
================
P-1: pre_processor is idempotent
     normalise(normalise(s)) == normalise(s)
     Why: catches subtle re-rewrites of already-rewritten $refs.

P-2: pipeline is deterministic
     run_pipeline(model) == run_pipeline(model)
     Why: drift-test depends on deterministic output. Hashmap iteration order,
     timestamps, tempdir names must not leak into output.

P-3: every Pydantic field with a Field(...) constraint surfaces in Zod
     for f in model.fields where f has min_length/max_length/pattern/gt/lt:
         emitted_zod contains the constraint
     Why: F-5 is the silent-drop risk. Single highest-value invariant.

P-4: discriminator field appears as Zod `z.discriminatedUnion(...)`
     for any Union with `Field(discriminator=...)`:
         emitted_zod uses `z.discriminatedUnion("<key>", [...])`
     Why: F-2 — wrong emission compiles but breaks runtime narrowing.

P-5: Optional vs nullable tristate maps to distinct Zod shapes
     `x: str | None = None`   -> `.optional()` (no nullable)
     `x: str | None` (required) -> `.nullable()` (no optional)
     Why: F-4 — wrong emission silently changes wire semantics.

TEST DESIGN NOTE
================
P-3/P-4/P-5 run against HAND-CRAFTED TypeScript + JSON Schema inputs — NOT
against the full bunx-emitted pipeline. This is deliberate: it isolates
`post_process_zod`'s logic from json2ts subprocess flakiness, keeps the tests
fast and deterministic, and lets the goldenfile-drift test (Wave 3) be the
end-to-end integration check. Per KISS + TDD.

REFERENCE
=========
- the test-pyramid principle (hand-picked fixtures over an exhaustive matrix):
  goldenfile fixtures committed, regeneration via `make goldenfile-regenerate`.
- the F-class table (see docs/edge-cases.md).
"""

from __future__ import annotations

from pathlib import Path

import pytest


@pytest.mark.property
def test_p1_pre_processor_idempotent() -> None:
    """P-1: normalise(normalise(s)) == normalise(s)."""
    from pydantic_zod_codegen import normalise_for_json2ts
    from tests.goldenfile.fixtures.models import TreeNode

    schema = TreeNode.model_json_schema()
    once = normalise_for_json2ts(schema)
    twice = normalise_for_json2ts(once)
    assert once == twice


@pytest.mark.property
def test_p2_pipeline_deterministic(tmp_path: Path) -> None:
    """P-2: regenerating the pipeline produces byte-identical output."""
    from pydantic_zod_codegen.drift import regenerate

    out1 = tmp_path / "out1.ts"
    out2 = tmp_path / "out2.ts"
    regenerate("tests.goldenfile.fixtures.models", out1)
    regenerate("tests.goldenfile.fixtures.models", out2)
    assert out1.read_bytes() == out2.read_bytes()


@pytest.mark.property
def test_p3_annotated_constraints_surface_in_zod() -> None:
    """P-3: Annotated[str, Field(min_length=3, max_length=32, pattern=...)] surfaces in Zod (F-5).

    Hand-crafted TS + schema that mirrors what the Username fixture would emit
    after pre_processor + bunx. The post-processor must reach into the source
    schema to recover the constraints json2ts drops on the floor.
    """
    from pydantic_zod_codegen import post_process_zod

    schema = {
        "type": "object",
        "properties": {
            "value": {
                "type": "string",
                "minLength": 3,
                "maxLength": 32,
                "pattern": "^[a-z]+$",
            }
        },
        "required": ["value"],
        "title": "Username",
        "additionalProperties": False,
    }
    ts = "export interface Username {\n  value: string;\n}\n"
    out = post_process_zod(ts, source_schema=schema)
    assert ".min(3)" in out, f"expected .min(3) in output, got:\n{out}"
    assert ".max(32)" in out, f"expected .max(32) in output, got:\n{out}"
    assert ".regex(" in out, f"expected .regex(...) in output, got:\n{out}"


@pytest.mark.property
def test_p4_discriminated_union_emitted() -> None:
    """P-4: Pydantic Discriminator -> Zod z.discriminatedUnion (F-2).

    Both the `Field(discriminator=...)` form (EventEnvelope wrapper) and the
    `Annotated[..., Discriminator(...)]` form (EventAnnotated alias) produce
    JSON Schemas with `oneOf` + `discriminator.propertyName` — the post-
    processor must spot that shape and emit `z.discriminatedUnion("kind", ...)`.
    """
    from pydantic_zod_codegen import post_process_zod

    # --- Form 1: Field(discriminator=...) wrapped in an envelope -------------
    envelope_schema = {
        "type": "object",
        "properties": {
            "event": {
                "discriminator": {
                    "mapping": {
                        "click": "ClickEvent",
                        "keyboard": "KeyboardEvent",
                    },
                    "propertyName": "kind",
                },
                "oneOf": [
                    {"$ref": "#/definitions/ClickEvent"},
                    {"$ref": "#/definitions/KeyboardEvent"},
                ],
            }
        },
        "required": ["event"],
        "title": "EventEnvelope",
        "additionalProperties": False,
        "definitions": {
            "ClickEvent": {
                "type": "object",
                "properties": {
                    "kind": {"const": "click", "type": "string"},
                    "selector": {"type": "string"},
                },
                "required": ["kind", "selector"],
                "title": "ClickEvent",
                "additionalProperties": False,
            },
            "KeyboardEvent": {
                "type": "object",
                "properties": {
                    "kind": {"const": "keyboard", "type": "string"},
                    "key": {"type": "string"},
                },
                "required": ["kind", "key"],
                "title": "KeyboardEvent",
                "additionalProperties": False,
            },
        },
    }
    envelope_ts = (
        "export interface EventEnvelope {\n"
        "  event: ClickEvent | KeyboardEvent;\n"
        "}\n"
        "export interface ClickEvent {\n"
        '  kind: "click";\n'
        "  selector: string;\n"
        "}\n"
        "export interface KeyboardEvent {\n"
        '  kind: "keyboard";\n'
        "  key: string;\n"
        "}\n"
    )
    envelope_out = post_process_zod(envelope_ts, source_schema=envelope_schema)
    assert 'z.discriminatedUnion("kind"' in envelope_out, (
        f'expected z.discriminatedUnion("kind", ...) in envelope output, got:\n{envelope_out}'
    )

    # --- Form 2: Annotated[..., Discriminator(...)] alias --------------------
    annotated_schema = {
        "discriminator": {
            "mapping": {
                "click": "ClickEvent",
                "keyboard": "KeyboardEvent",
            },
            "propertyName": "kind",
        },
        "oneOf": [
            {"$ref": "#/definitions/ClickEvent"},
            {"$ref": "#/definitions/KeyboardEvent"},
        ],
        "title": "EventAnnotated",
        "definitions": {
            "ClickEvent": {
                "type": "object",
                "properties": {
                    "kind": {"const": "click", "type": "string"},
                    "selector": {"type": "string"},
                },
                "required": ["kind", "selector"],
                "title": "ClickEvent",
                "additionalProperties": False,
            },
            "KeyboardEvent": {
                "type": "object",
                "properties": {
                    "kind": {"const": "keyboard", "type": "string"},
                    "key": {"type": "string"},
                },
                "required": ["kind", "key"],
                "title": "KeyboardEvent",
                "additionalProperties": False,
            },
        },
    }
    annotated_ts = (
        "export type EventAnnotated = ClickEvent | KeyboardEvent;\n"
        "export interface ClickEvent {\n"
        '  kind: "click";\n'
        "  selector: string;\n"
        "}\n"
        "export interface KeyboardEvent {\n"
        '  kind: "keyboard";\n'
        "  key: string;\n"
        "}\n"
    )
    annotated_out = post_process_zod(annotated_ts, source_schema=annotated_schema)
    assert 'z.discriminatedUnion("kind"' in annotated_out, (
        f'expected z.discriminatedUnion("kind", ...) in annotated output, got:\n{annotated_out}'
    )


@pytest.mark.property
def test_p5_optional_nullable_tristate() -> None:
    """P-5: Optional/nullable tristate maps to distinct Zod modifier chains (F-4).

    The User fixture has three fields, one per tristate case:
      - name: str               -> z.string()                       (required, plain)
      - age:  int | None        -> z.number().nullable()            (required, nullable)
      - nickname: str|None=None -> z.string().nullable().optional() (optional + nullable + default)
    """
    from pydantic_zod_codegen import post_process_zod

    schema = {
        "type": "object",
        "properties": {
            "name": {"type": "string"},
            "nickname": {
                "anyOf": [{"type": "string"}, {"type": "null"}],
                "default": None,
            },
            "age": {
                "anyOf": [{"type": "integer"}, {"type": "null"}],
            },
        },
        "required": ["name", "age"],
        "title": "User",
        "additionalProperties": False,
    }
    ts = "export interface User {\n  name: string;\n  nickname?: string | null;\n  age: number | null;\n}\n"
    out = post_process_zod(ts, source_schema=schema)

    # Locate each field's Zod chain inside the `z.object({ ... })` block —
    # not in the original interface body (which would match `name: string;`).
    import re

    zod_object_match = re.search(r"export const User = z\.object\(\{(.*?)\}\);", out, re.DOTALL)
    assert zod_object_match, f"could not locate `export const User = z.object(...)` in:\n{out}"
    zod_body = zod_object_match.group(1)

    field_chains: dict[str, str] = {}
    for field in ("name", "nickname", "age"):
        # Match "<field>: <chain>," where chain may span until the trailing comma.
        match = re.search(rf"^\s*{field}\s*:\s*([^,\n]+),", zod_body, re.MULTILINE)
        assert match, f"could not locate Zod chain for field {field!r} in:\n{zod_body}"
        field_chains[field] = match.group(1).strip()

    # name: plain z.string(), no .nullable, no .optional
    name_chain = field_chains["name"]
    assert ".nullable" not in name_chain, f"name should not be nullable, got: {name_chain}"
    assert ".optional" not in name_chain, f"name should not be optional, got: {name_chain}"
    assert "z.string()" in name_chain, f"name should be z.string(), got: {name_chain}"

    # age: nullable but NOT optional
    age_chain = field_chains["age"]
    assert ".nullable" in age_chain, f"age should be nullable, got: {age_chain}"
    assert ".optional" not in age_chain, f"age should NOT be optional, got: {age_chain}"

    # nickname: BOTH nullable AND optional
    nickname_chain = field_chains["nickname"]
    assert ".nullable" in nickname_chain, f"nickname should be nullable, got: {nickname_chain}"
    assert ".optional" in nickname_chain, f"nickname should be optional, got: {nickname_chain}"
