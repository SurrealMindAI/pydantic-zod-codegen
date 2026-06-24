"""Unit tests for `CustomGenerateJsonSchema` overrides.

Each test guards exactly one override. Scope-limited per the test-pyramide
discipline — these are smoke checks, not exhaustive matrices. The P-1..P-5
property tests in `test_round_trip.py` exercise the overrides end-to-end via
the full pipeline.

Override coverage:
    - `field_title_should_be_set` -> always False (noise suppression)
    - `tagged_union_schema` -> strips `#/$defs/` prefix from `mapping` values
    - `generate` -> injects `additionalProperties: false` on every object node
    - `model_parametrized_name` -> `Page[Item]` -> `PageOfItem` (no underscores)

REFERENCE
=========
- src/pydantic_zod_codegen/custom_schema.py — the module under test
- tests/goldenfile/fixtures/models.py — the F-class canonical models
"""

from __future__ import annotations

from typing import Any

from pydantic_zod_codegen import CustomGenerateJsonSchema
from tests.goldenfile.fixtures.models import EventEnvelope, Item, Page, User


def test_field_title_should_be_set_returns_false() -> None:
    """Protocol convention: suppress auto-generated field titles unconditionally."""
    generator = CustomGenerateJsonSchema()
    # The schema arg is unused by our override — pass an empty dict-like as a
    # CoreSchemaOrField placeholder. The real call site passes a CoreSchemaOrField,
    # but our override ignores the argument.
    schema: Any = {"type": "str"}
    assert generator.field_title_should_be_set(schema) is False


def test_tagged_union_strips_defs_prefix() -> None:
    """F-2: discriminator `mapping` values must be bare type names, not `#/$defs/...`."""
    schema = EventEnvelope.model_json_schema(schema_generator=CustomGenerateJsonSchema)

    # Locate the discriminator node. The `event` property is a discriminated union;
    # Pydantic emits a `discriminator` object on the property schema.
    event_property = schema["properties"]["event"]
    discriminator = event_property["discriminator"]
    mapping = discriminator["mapping"]

    assert mapping["click"] == "ClickEvent"
    assert mapping["keyboard"] == "KeyboardEvent"
    for value in mapping.values():
        assert not value.startswith("#/$defs/"), f"mapping value {value!r} still has $defs prefix"


def test_generate_injects_additional_properties_false() -> None:
    """F-6: every `type: object` node must carry `additionalProperties: false`."""
    schema = User.model_json_schema(schema_generator=CustomGenerateJsonSchema)

    object_nodes = list(_iter_object_nodes(schema))
    assert object_nodes, "expected at least one object node in User's schema"
    for node in object_nodes:
        assert node.get("additionalProperties") is False, f"object node missing `additionalProperties: false`: {node!r}"


def test_model_parametrized_name_camel_case() -> None:
    """F-3 naming: `Page[Item]` -> `PageOfItem` (no underscores, no brackets)."""
    name = CustomGenerateJsonSchema().model_parametrized_name(Page, (Item,))
    assert name == "PageOfItem"


def _iter_object_nodes(node: Any) -> Any:
    """Yield every dict subnode whose `type == "object"` from a JSON Schema tree."""
    if isinstance(node, dict):
        if node.get("type") == "object":
            yield node
        for value in node.values():
            yield from _iter_object_nodes(value)
    elif isinstance(node, list):
        for item in node:
            yield from _iter_object_nodes(item)
