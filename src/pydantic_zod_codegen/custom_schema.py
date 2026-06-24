"""Pydantic-side schema generator subclass — normalizes JSON Schema output before json2ts sees it.

Modelled after FastUI's `CustomGenerateJsonSchema` (pydantic team's own production pattern).

WHY THIS MODULE EXISTS
======================
Pydantic v2.13's `BaseModel.model_json_schema()` produces output that is correct
JSON-Schema 2020-12 but has shape mismatches with what `json-schema-to-typescript@15`
expects:

- F-1 (recursive): emits `#/$defs/X` references which json2ts cannot resolve
  cleanly (URL-encoding bug at `%24defs`). The bulk rewrite happens in
  `pre_processor.py`; this module owns the per-model schema hooks.
- F-2 (discriminated union): `tagged_union_schema` emits `mapping` entries with
  `#/$defs/` prefix; we strip it so downstream consumers get bare type names.
- F-5 (annotated metadata): `field_title_should_be_set` is hooked to suppress
  noise titles from `Annotated[str, Field(...)]` shapes.
- F-6 (additionalProperties): enforce protocol convention `additionalProperties: false`
  on every object-typed schema node.
- F-3 (generic naming): `Page[Item]` should mangle to `PageOfItem` rather than
  Pydantic's default `Page[Item]` / `Page_Item_` — kebab-friendly identifiers
  for the emitted TypeScript / Zod identifiers.

REFERENCE
=========
- the failure-mode taxonomy (see docs/edge-cases.md)
- FastUI's `CustomGenerateJsonSchema` pattern (Pydantic team's production usage)
- Pydantic v2.13 GenerateJsonSchema API: https://docs.pydantic.dev/latest/api/json_schema/
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel
from pydantic.json_schema import GenerateJsonSchema, JsonSchemaMode, JsonSchemaValue
from pydantic_core import CoreSchema, core_schema


class CustomGenerateJsonSchema(GenerateJsonSchema):
    """Subclass of Pydantic's `GenerateJsonSchema` with codegen-friendly overrides.

    Usage:
        schema = MyModel.model_json_schema(schema_generator=CustomGenerateJsonSchema)
    """

    def field_title_should_be_set(self, schema: Any) -> bool:  # noqa: ARG002
        """Protocol convention: never auto-emit field titles.

        Field names are already the title — auto-titles only add noise to json2ts
        output (duplicated identifiers in JSDoc, longer `.gen.ts` diffs).
        """
        return False

    def tagged_union_schema(self, schema: core_schema.TaggedUnionSchema) -> JsonSchemaValue:
        """F-2: strip the `#/$defs/` prefix from discriminator `mapping` values (first pass).

        Pydantic emits the mapping as `{"click": "#/$defs/ClickEvent", ...}`. We strip the
        prefix here so the in-flight schema carries bare type names. NOTE: Pydantic runs a
        post-processing `$defs` rename pass AFTER this hook returns; that pass restores the
        prefix as part of normalizing internal names to user-facing ones. The final cleanup
        therefore happens in `generate` — but stripping here too documents intent and keeps
        the partial output consistent for any other hook that observes it.
        """
        result = super().tagged_union_schema(schema)
        mapping = result.get("mapping")
        if isinstance(mapping, dict):
            result["mapping"] = {key: _strip_defs_prefix(value) for key, value in mapping.items()}
        return result

    def generate(self, schema: CoreSchema, mode: JsonSchemaMode = "validation") -> JsonSchemaValue:
        """Final-pass normalization on the fully-assembled JSON Schema.

        Two responsibilities, both depending on the schema being fully assembled:
          - F-6: inject `additionalProperties: false` on every object-typed node where
            it is not already set (preserves explicit user-set values).
          - F-2: strip `#/$defs/` prefix from every discriminator `mapping` value.
            Pydantic restores the prefix during its post-`tagged_union_schema` rename
            pass, so the final fix must happen here.
        """
        result = super().generate(schema, mode=mode)
        _inject_additional_properties_false(result)
        _strip_discriminator_mapping_prefix(result)
        return result

    def model_parametrized_name(self, cls: type[BaseModel], params: tuple[type[Any], ...]) -> str:
        """F-3: render `Page[Item]` as `PageOfItem` (kebab-friendly identifier).

        Default Pydantic naming is `Page[Item]`, which is invalid as a TypeScript
        identifier; the underscored variant `Page_Item_` is review-hostile. We
        join the parameter type names with `Of` for a clean, readable identifier.
        """
        param_names = "".join(param.__name__ for param in params)
        return f"{cls.__name__}Of{param_names}"


def _strip_defs_prefix(ref: str) -> str:
    """Drop the leading `#/$defs/` from a JSON-Schema `$ref`-style string.

    Idempotent: bare names pass through unchanged.
    """
    prefix = "#/$defs/"
    if ref.startswith(prefix):
        return ref[len(prefix) :]
    return ref


def _inject_additional_properties_false(node: Any) -> None:
    """Recursively set `additionalProperties: false` on every `type: object` dict.

    Mutates `node` in place. Preserves explicit user-set values — if a node
    already has `additionalProperties` set (to anything, including `True`),
    it is left untouched.
    """
    if isinstance(node, dict):
        if node.get("type") == "object" and "additionalProperties" not in node:
            node["additionalProperties"] = False
        for value in node.values():
            _inject_additional_properties_false(value)
    elif isinstance(node, list):
        for item in node:
            _inject_additional_properties_false(item)


def _strip_discriminator_mapping_prefix(node: Any) -> None:
    """Recursively strip `#/$defs/` from every `discriminator.mapping` value.

    Mutates `node` in place. Only touches dicts with a `mapping` key under a
    `discriminator` parent — other `mapping` keys are left alone.
    """
    if isinstance(node, dict):
        discriminator = node.get("discriminator")
        if isinstance(discriminator, dict):
            mapping = discriminator.get("mapping")
            if isinstance(mapping, dict):
                discriminator["mapping"] = {key: _strip_defs_prefix(value) for key, value in mapping.items()}
        for value in node.values():
            _strip_discriminator_mapping_prefix(value)
    elif isinstance(node, list):
        for item in node:
            _strip_discriminator_mapping_prefix(item)
