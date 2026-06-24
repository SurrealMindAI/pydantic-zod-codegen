"""Regression tests for post_processor.py — bugs surfaced via ultrareview 2026-05-15.

Each test names the bug it guards. Failing-first TDD: write the test, see it
fail, then fix. These tests target the pure functions directly (not via the
full pipeline) for fast feedback.

Goldenfile-drift impact:
    - BUG-3, BUG-6: pure functions, no fixture triggers them today → no drift.
    - BUG-4: User fixture has `nickname: str | None = None`; the fix emits an
      explicit `.default(null)` that the current goldenfile lacks → drift.
    - BUG-2, BUG-5: no current fixture triggers them → no drift.
"""

from __future__ import annotations

import re

from pydantic_zod_codegen import post_process_zod
from pydantic_zod_codegen.post_processor import (
    _constraint_chain,
    _json_to_ts_literal,
)

# ============================================================================
# BUG-3: _json_to_ts_literal must escape control characters
# ============================================================================


def test_bug3_newline_escaped() -> None:
    """\\n in a default value becomes the escape sequence \\\\n, not literal LF.

    Without this, the emitted TS string literal breaks across lines:
        description: z.string().default("line1
        line2"),
    -> tsc raises `Unterminated string literal`.
    """
    out = _json_to_ts_literal("a\nb")
    assert "\n" not in out[1:-1], f"literal LF leaked through: {out!r}"
    assert out == r'"a\nb"', f"unexpected output: {out!r}"


def test_bug3_tab_escaped() -> None:
    out = _json_to_ts_literal("a\tb")
    assert "\t" not in out, f"literal TAB leaked through: {out!r}"


def test_bug3_carriage_return_escaped() -> None:
    out = _json_to_ts_literal("a\rb")
    assert "\r" not in out, f"literal CR leaked through: {out!r}"


def test_bug3_backslash_still_escaped() -> None:
    """Regression: existing backslash escape continues to work."""
    out = _json_to_ts_literal("a\\b")
    assert out == r'"a\\b"', f"unexpected output: {out!r}"


def test_bug3_doublequote_still_escaped() -> None:
    """Regression: existing double-quote escape continues to work."""
    out = _json_to_ts_literal('a"b')
    assert out == r'"a\"b"', f"unexpected output: {out!r}"


def test_bug3_paragraph_separator_escaped() -> None:
    """U+2028/2029 are illegal as unescaped chars in JS string literals pre-ES2019."""
    out = _json_to_ts_literal("a b")
    assert " " not in out, f"raw U+2028 leaked: {out!r}"
    out2 = _json_to_ts_literal("a b")
    assert " " not in out2, f"raw U+2029 leaked: {out2!r}"


# ============================================================================
# BUG-6: forward slash in regex pattern must be escaped
# ============================================================================


def test_bug6_url_pattern_escapes_slashes() -> None:
    r"""A `pattern` containing `/` must produce a valid JS regex literal.

    Common URL/path validators trigger this:
        Field(pattern=r"^https?://.+$")
    Naive `.regex(/^https?://.+$/)` parses as `.regex(/^https?:/, .+$, ...)` —
    early-terminated regex → SyntaxError. Either escape unescaped `/` to `\/`
    inside the delimited form, or switch to `new RegExp("...")` form.
    """
    chain = _constraint_chain({"pattern": r"^https?://.+$"})
    match = re.search(r"\.regex\((.*)\)", chain)
    assert match, f"expected `.regex(...)` in chain, got: {chain!r}"
    inner = match.group(1)
    if inner.startswith("/"):
        # Delimited-form `/.../`: body must not contain unescaped `/`.
        assert inner.endswith("/"), f"malformed regex literal: {inner!r}"
        body = inner[1:-1]
        unescaped = re.search(r"(?<!\\)/", body)
        assert unescaped is None, f"unescaped `/` at idx {unescaped.start() if unescaped else -1} in body: {body!r}"
    elif inner.startswith("new RegExp("):
        # `new RegExp("...")` form sidesteps delimiter parsing — accepted.
        pass
    else:
        raise AssertionError(f"unexpected `.regex` payload form: {inner!r}")


def test_bug6_pattern_without_slashes_still_works() -> None:
    """Regression: the Username fixture path (`^[a-z]+$`) keeps emitting cleanly."""
    chain = _constraint_chain({"pattern": r"^[a-z]+$"})
    assert ".regex(/^[a-z]+$/)" in chain, f"unexpected chain: {chain!r}"


def test_bug6_pattern_with_escaped_slash_preserved() -> None:
    """A pattern already containing `\\/` must not double-escape."""
    chain = _constraint_chain({"pattern": r"^\/api\/.+$"})
    match = re.search(r"\.regex\((.*)\)", chain)
    assert match
    inner = match.group(1)
    if inner.startswith("/") and inner.endswith("/"):
        body = inner[1:-1]
        unescaped = re.search(r"(?<!\\)/", body)
        assert unescaped is None, f"unescaped `/` after re-escape: {body!r}"


# ============================================================================
# BUG-4: nullable+optional fields keep their default value
# ============================================================================


def _extract_field_chain(out: str, field_name: str) -> str:
    match = re.search(rf"^\s*{field_name}\s*:\s*([^,\n]+),", out, re.MULTILINE)
    assert match, f"could not locate `{field_name}` chain in:\n{out}"
    return match.group(1).strip()


def test_bug4_nullable_optional_with_int_default() -> None:
    """`timeout: int | None = 30` -> `.nullable().optional().default(30)`."""
    schema = {
        "type": "object",
        "properties": {
            "timeout": {
                "anyOf": [{"type": "integer"}, {"type": "null"}],
                "default": 30,
            },
        },
        "required": [],
        "title": "Config",
        "additionalProperties": False,
    }
    ts = "export interface Config {\n  timeout?: number | null;\n}\n"
    out = post_process_zod(ts, source_schema=schema)
    chain = _extract_field_chain(out, "timeout")
    assert ".nullable" in chain, f"missing .nullable in: {chain}"
    assert ".optional" in chain, f"missing .optional in: {chain}"
    assert ".default(30)" in chain, f"missing .default(30) in: {chain}"


def test_bug4_nullable_optional_with_string_default() -> None:
    """`nickname: str | None = "anon"` -> `.nullable().optional().default("anon")`."""
    schema = {
        "type": "object",
        "properties": {
            "nickname": {
                "anyOf": [{"type": "string"}, {"type": "null"}],
                "default": "anon",
            },
        },
        "required": [],
        "title": "User",
        "additionalProperties": False,
    }
    ts = "export interface User {\n  nickname?: string | null;\n}\n"
    out = post_process_zod(ts, source_schema=schema)
    chain = _extract_field_chain(out, "nickname")
    assert '.default("anon")' in chain, f"got: {chain}"


def test_bug4_required_nullable_no_default_unchanged() -> None:
    """Regression: required+nullable without default stays `.nullable()` only."""
    schema = {
        "type": "object",
        "properties": {
            "age": {"anyOf": [{"type": "integer"}, {"type": "null"}]},
        },
        "required": ["age"],
        "title": "User",
        "additionalProperties": False,
    }
    ts = "export interface User {\n  age: number | null;\n}\n"
    out = post_process_zod(ts, source_schema=schema)
    chain = _extract_field_chain(out, "age")
    assert ".nullable" in chain
    assert ".optional" not in chain
    assert ".default" not in chain


# ============================================================================
# BUG-2: nested $ref field must reference the model name, not z.object({})
# ============================================================================


def test_bug2_nested_ref_emits_model_name() -> None:
    """`inner: Inner` -> `inner: Inner`, NOT `inner: z.object({})`.

    Common Pydantic composition pattern: a field whose type is another
    BaseModel. Pydantic emits the field as `{"$ref": "#/definitions/Inner"}`.
    The post-processor must surface that as a name reference, not the empty
    object literal.
    """
    schema = {
        "type": "object",
        "title": "Outer",
        "additionalProperties": False,
        "properties": {
            "inner": {"$ref": "#/definitions/Inner"},
        },
        "required": ["inner"],
        "definitions": {
            "Inner": {
                "type": "object",
                "title": "Inner",
                "additionalProperties": False,
                "properties": {"x": {"type": "integer"}},
                "required": ["x"],
            },
        },
    }
    ts = "export interface Outer {\n  inner: Inner;\n}\nexport interface Inner {\n  x: number;\n}\n"
    out = post_process_zod(ts, source_schema=schema)
    chain = _extract_field_chain(out, "inner")
    assert chain == "Inner", f"expected bare `Inner`, got: {chain!r}"
    assert "z.object({})" not in out, f"found stub z.object({{}}) in:\n{out}"


def test_bug2_nullable_nested_ref() -> None:
    """`inner: Inner | None` -> `inner: Inner.nullable()` (NOT `z.object({}).nullable()`)."""
    schema = {
        "type": "object",
        "title": "Outer",
        "additionalProperties": False,
        "properties": {
            "inner": {
                "anyOf": [
                    {"$ref": "#/definitions/Inner"},
                    {"type": "null"},
                ],
            },
        },
        "required": ["inner"],
        "definitions": {
            "Inner": {
                "type": "object",
                "title": "Inner",
                "additionalProperties": False,
                "properties": {"x": {"type": "integer"}},
                "required": ["x"],
            },
        },
    }
    ts = "export interface Outer {\n  inner: Inner | null;\n}\nexport interface Inner {\n  x: number;\n}\n"
    out = post_process_zod(ts, source_schema=schema)
    chain = _extract_field_chain(out, "inner")
    assert chain.startswith("Inner"), f"expected to start with `Inner`, got: {chain!r}"
    assert ".nullable" in chain, f"missing .nullable in: {chain!r}"
    assert "z.object({})" not in out


# ============================================================================
# BUG-5: regex parsers must survive nested braces / inline objects
# ============================================================================


# ============================================================================
# BUG-1: emitted Zod consts must not crash with TDZ ReferenceError at import
# ============================================================================


def test_bug1_self_referential_const_wrapped_in_z_lazy() -> None:
    """Self-recursive const must use z.lazy to defer identifier resolution.

    `export const TreeNode = z.object({children: z.array(TreeNode)...});`
    reads `TreeNode` inside its own initializer while the binding is still in
    the TDZ → `ReferenceError: Cannot access 'TreeNode' before initialization`.
    Wrapping with `z.lazy(() => ...)` defers body evaluation until the schema
    is actually used, at which point the binding is initialized.
    """
    schema = {
        "type": "object",
        "title": "TreeNode",
        "additionalProperties": False,
        "properties": {
            "name": {"type": "string"},
            "children": {
                "type": "array",
                "items": {"$ref": "#/definitions/TreeNode"},
                "default": [],
            },
        },
        "required": ["name"],
        "definitions": {
            "TreeNode": {
                "type": "object",
                "title": "TreeNode",
                "additionalProperties": False,
                "properties": {
                    "name": {"type": "string"},
                    "children": {
                        "type": "array",
                        "items": {"$ref": "#/definitions/TreeNode"},
                        "default": [],
                    },
                },
                "required": ["name"],
            }
        },
    }
    ts = "export interface TreeNode {\n  name: string;\n  children?: TreeNode[];\n}\n"
    out = post_process_zod(ts, source_schema=schema)
    assert "z.lazy(() => z.object(" in out, f"missing z.lazy wrap for self-ref:\n{out}"
    assert "z.array(TreeNode)" in out, f"missing TreeNode self-reference:\n{out}"


def test_bug1_discriminated_union_root_emitted_after_variants() -> None:
    """In a chunk with root+variants, variant consts come BEFORE the root const.

    EventEnvelope's z.object body references `[ClickEvent, KeyboardEvent]`
    inside discriminatedUnion — those bindings must be initialized first.
    """
    schema = {
        "type": "object",
        "title": "EventEnvelope",
        "additionalProperties": False,
        "properties": {
            "event": {
                "discriminator": {
                    "mapping": {"click": "ClickEvent", "keyboard": "KeyboardEvent"},
                    "propertyName": "kind",
                },
                "oneOf": [
                    {"$ref": "#/definitions/ClickEvent"},
                    {"$ref": "#/definitions/KeyboardEvent"},
                ],
            },
        },
        "required": ["event"],
        "definitions": {
            "ClickEvent": {
                "type": "object",
                "title": "ClickEvent",
                "additionalProperties": False,
                "properties": {
                    "kind": {"const": "click", "type": "string"},
                    "selector": {"type": "string"},
                },
                "required": ["kind", "selector"],
            },
            "KeyboardEvent": {
                "type": "object",
                "title": "KeyboardEvent",
                "additionalProperties": False,
                "properties": {
                    "kind": {"const": "keyboard", "type": "string"},
                    "key": {"type": "string"},
                },
                "required": ["kind", "key"],
            },
        },
    }
    ts = (
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
    out = post_process_zod(ts, source_schema=schema)

    click_pos = out.find("export const ClickEvent")
    keyboard_pos = out.find("export const KeyboardEvent")
    envelope_pos = out.find("export const EventEnvelope")

    assert click_pos >= 0, f"missing ClickEvent const:\n{out}"
    assert keyboard_pos >= 0, f"missing KeyboardEvent const:\n{out}"
    assert envelope_pos >= 0, f"missing EventEnvelope const:\n{out}"
    assert click_pos < envelope_pos, (
        f"ClickEvent ({click_pos}) must precede EventEnvelope ({envelope_pos}) — TDZ violation"
    )
    assert keyboard_pos < envelope_pos, (
        f"KeyboardEvent ({keyboard_pos}) must precede EventEnvelope ({envelope_pos}) — TDZ violation"
    )


def test_bug1_non_self_referential_const_not_wrapped() -> None:
    """Regression: a const without self-references stays as plain `z.object(...)`."""
    schema = {
        "type": "object",
        "title": "User",
        "additionalProperties": False,
        "properties": {
            "name": {"type": "string"},
            "age": {"type": "integer"},
        },
        "required": ["name", "age"],
    }
    ts = "export interface User {\n  name: string;\n  age: number;\n}\n"
    out = post_process_zod(ts, source_schema=schema)
    assert "z.lazy" not in out, f"unexpected z.lazy in non-recursive output:\n{out}"


def test_bug5_interface_with_inline_object_body() -> None:
    """`metadata: { [k:string]: unknown }` must not truncate the surrounding interface body.

    json2ts emits an inline anonymous-object body for `dict[str, X]` fields.
    The `_INTERFACE_RE` body capture must traverse the inline `{...}` and
    surface every subsequent field. Otherwise `_emit_object_schema` writes
    `z.object({})` with the required fields silently missing.
    """
    schema = {
        "type": "object",
        "title": "Container",
        "additionalProperties": False,
        "properties": {
            "metadata": {
                "type": "object",
                "additionalProperties": {"type": "string"},
            },
            "name": {"type": "string"},
        },
        "required": ["metadata", "name"],
    }
    ts = "export interface Container {\n  metadata: {\n    [k: string]: string;\n  };\n  name: string;\n}\n"
    out = post_process_zod(ts, source_schema=schema)
    container_match = re.search(r"export const Container = z\.object\(\{(.*?)\}\);", out, re.DOTALL)
    assert container_match, f"missing Container const in:\n{out}"
    body = container_match.group(1)
    assert "name:" in body, f"`name` field was dropped from Container const:\n{body}"
    assert "metadata:" in body, f"`metadata` field was dropped from Container const:\n{body}"


# ============================================================================
# BUG-7: JSDoc comments preceding interface fields cause silent field drop
# ============================================================================


def test_bug7_required_field_with_jsdoc_not_dropped() -> None:
    """A required field whose declaration is preceded by a JSDoc block must appear in z.object.

    json2ts emits per-property JSDoc INSIDE the interface body whenever the
    Pydantic Field has a description/annotation. The current `_parse_interfaces`
    splits the body on top-level `;` and then anchors the field-signature regex
    at `^`. The leading `/**...*/` then prevents the regex from matching the
    field-name, so the field is silently dropped and `_emit_object_schema`
    omits it from `z.object(...)`. Required fields without defaults thus
    disappear from the runtime schema, leaving the type-level interface
    out-of-sync with the value-level const (which is exactly what a real-world
    protocol hit on a metadata-only field like `OverlayReady.bundle_build_session`,
    i.e. a `str = Field(description=...)` with no default).
    """
    schema = {
        "type": "object",
        "title": "OverlayReady",
        "additionalProperties": False,
        "properties": {
            "bundle_build_session": {"type": "string"},
        },
        "required": ["bundle_build_session"],
    }
    ts = "export interface OverlayReady {\n  /**\n   * Build-session UUID.\n   */\n  bundle_build_session: string;\n}\n"
    out = post_process_zod(ts, source_schema=schema)
    container_match = re.search(r"export const OverlayReady = z\.object\(\{(.*?)\}\);", out, re.DOTALL)
    assert container_match, f"missing OverlayReady const in:\n{out}"
    body = container_match.group(1)
    assert "bundle_build_session" in body, f"required field dropped due to JSDoc prefix:\n{body}"


def test_bug7_all_fields_jsdocced_emits_full_object() -> None:
    """When EVERY field has a JSDoc, the emitted z.object must still contain all of them.

    This is the strict version of bug7 — without the fix the entire body is
    `z.object({})` because every field is parsed as an invalid signature.
    """
    schema = {
        "type": "object",
        "title": "ElementRect",
        "additionalProperties": False,
        "properties": {
            "x": {"type": "integer"},
            "y": {"type": "integer"},
        },
        "required": ["x", "y"],
    }
    ts = (
        "export interface ElementRect {\n"
        "  /** X coordinate. */\n"
        "  x: number;\n"
        "  /** Y coordinate. */\n"
        "  y: number;\n"
        "}\n"
    )
    out = post_process_zod(ts, source_schema=schema)
    container_match = re.search(r"export const ElementRect = z\.object\(\{(.*?)\}\);", out, re.DOTALL)
    assert container_match, f"missing ElementRect const in:\n{out}"
    body = container_match.group(1)
    assert "x:" in body, f"`x` field dropped:\n{body}"
    assert "y:" in body, f"`y` field dropped:\n{body}"


def test_bug7_single_line_jsdoc_handled() -> None:
    """`/** comment */` on the SAME line as the field declaration must also be stripped."""
    schema = {
        "type": "object",
        "title": "Foo",
        "additionalProperties": False,
        "properties": {"x": {"type": "integer"}},
        "required": ["x"],
    }
    ts = "export interface Foo {\n  /** doc */ x: number;\n}\n"
    out = post_process_zod(ts, source_schema=schema)
    container_match = re.search(r"export const Foo = z\.object\(\{(.*?)\}\);", out, re.DOTALL)
    assert container_match, f"missing Foo const in:\n{out}"
    assert "x:" in container_match.group(1), f"single-line-JSDoc'd field dropped:\n{out}"


# ============================================================================
# BUG-9: transitive `$ref` chains must be emitted in topological order
# ============================================================================


def test_bug9_transitive_ref_topo_order() -> None:
    """A chunk with chain A → B → C must emit consts in order C, B, A (root last).

    Pydantic models like ``Pick { element: PickElement { fingerprint: ElementFingerprint } }``
    have a multi-step composition chain. json2ts emits the interfaces in DFS-from-root
    order (root first). The previous post-processor sort only moved the root to the
    end; the variants kept json2ts's order, so ``Pick = z.object({ element: PickElement })``
    was emitted BEFORE ``PickElement = z.object(...)``. At module load TypeScript flags
    this as `Block-scoped variable 'PickElement' used before its declaration` and the
    Zod const fails to construct at runtime (TDZ).

    Every referenced const must precede the consts that reference it; the root chunk
    sits last so it sees all its transitive deps.
    """
    schema = {
        "type": "object",
        "title": "Pick",
        "additionalProperties": False,
        "properties": {"element": {"$ref": "#/definitions/PickElement"}},
        "required": ["element"],
        "definitions": {
            "PickElement": {
                "type": "object",
                "title": "PickElement",
                "additionalProperties": False,
                "properties": {"fingerprint": {"$ref": "#/definitions/ElementFingerprint"}},
                "required": ["fingerprint"],
            },
            "ElementFingerprint": {
                "type": "object",
                "title": "ElementFingerprint",
                "additionalProperties": False,
                "properties": {"tag": {"type": "string"}},
                "required": ["tag"],
            },
        },
    }
    ts = (
        "export interface Pick {\n  element: PickElement;\n}\n"
        "export interface PickElement {\n  fingerprint: ElementFingerprint;\n}\n"
        "export interface ElementFingerprint {\n  tag: string;\n}\n"
    )
    out = post_process_zod(ts, source_schema=schema)

    ef_pos = out.find("export const ElementFingerprint")
    pe_pos = out.find("export const PickElement")
    pick_pos = out.find("export const Pick =")

    assert ef_pos >= 0, f"missing ElementFingerprint const:\n{out}"
    assert pe_pos >= 0, f"missing PickElement const:\n{out}"
    assert pick_pos >= 0, f"missing Pick const:\n{out}"
    assert ef_pos < pe_pos, f"ElementFingerprint ({ef_pos}) must precede PickElement ({pe_pos}) — TDZ violation"
    assert pe_pos < pick_pos, f"PickElement ({pe_pos}) must precede Pick ({pick_pos}) — TDZ violation"
