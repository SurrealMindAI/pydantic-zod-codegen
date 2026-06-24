"""JSON-Schema pre-processor — rewrites Pydantic output before handing to json2ts.

WHY THIS MODULE EXISTS
======================
Empirical finding (see docs/edge-cases.md): Pydantic 2.12+
emits `#/$defs/...` references which `json-schema-to-typescript@15` URL-encodes
as `%24defs` and then cannot resolve. Recursive models crash; we cannot ship
without this 5-line rewrite.

F-1 (recursive models): TreeNode { children: list["TreeNode"] } crashes baseline json2ts.

THIS FILE IS DELIBERATELY MINIMAL
=================================
Empirically required: the `$defs -> definitions` rewrite. Empirically optional
or model-specific: top-level $ref promotion, discriminator mapping prefix
stripping. The implementer adds those as needed — start with the 5-line core.

REFERENCE
=========
- the failure-mode taxonomy (see docs/edge-cases.md)
- the hand-rolled emitter prototype (see docs/edge-cases.md)
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any


def normalise_for_json2ts(schema: dict[str, Any]) -> dict[str, Any]:
    """Rewrite Pydantic-2.13+ JSON Schema for json2ts@15 compatibility (F-1).

    json2ts's URL encoder crashes on Pydantic's `$defs` + `#/$defs/...` $ref syntax
    on recursive schemas. The fix is to rename `$defs` -> `definitions` at the top
    level and rewrite every `$ref` value from `#/$defs/X` to `#/definitions/X`.

    Idempotent: re-running this function on its own output is a no-op (after the
    first pass no `$defs` key remains and no `#/$defs/`-prefixed $refs remain).

    Args:
        schema: a Pydantic `model_json_schema()` dict. Not mutated.

    Returns:
        a new dict, ready for `json-schema-to-typescript`.
    """
    out = deepcopy(schema)
    if "$defs" in out:
        out["definitions"] = out.pop("$defs")
    _rewrite_refs(out, src="#/$defs/", dst="#/definitions/")
    return out


def _rewrite_refs(node: Any, *, src: str, dst: str) -> None:
    """Recursive in-place `$ref` rewrite. Handles dict, list, leaf values."""
    if isinstance(node, dict):
        for key, value in node.items():
            if key == "$ref" and isinstance(value, str) and value.startswith(src):
                node[key] = dst + value[len(src) :]
            else:
                _rewrite_refs(value, src=src, dst=dst)
    elif isinstance(node, list):
        for item in node:
            _rewrite_refs(item, src=src, dst=dst)
    # leaf values (str, int, bool, None) — no-op
