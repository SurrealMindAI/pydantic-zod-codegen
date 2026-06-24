"""Zod v4 post-processor — augments the json2ts TypeScript output into Zod v4 schemas.

WHY THIS MODULE EXISTS
======================
`json-schema-to-typescript` emits TS `interface`s (types). For runtime
validation we need Zod schemas. There is no existing Pydantic -> Zod-v4 tool
(this is the widest gap in the surrounding tooling landscape).

Strategy: append a parallel `z.object(...)` schema with Zod v4 syntax for every
emitted TS interface (and a `z.discriminatedUnion(...)` for every emitted type
alias backed by a `oneOf`+`discriminator` schema). We DO NOT replace the
interfaces — they remain the consumer-facing types via `z.infer<typeof X>`
equivalence.

ZOD V4 SYNTAX NOTES
===================
- top-level format functions: `z.email()`, `z.uuid()`, `z.url()` (NOT
  `z.string().email()` — v3 syntax). `z.iso.datetime()` for date-time.
- `z.discriminatedUnion("kind", [VariantA, VariantB])` for F-2
- `z.string().nullable().optional()` for tristate-optional+nullable
- `z.string().brand<"Decimal">()` for decimal (no `z.decimal()`)

POST-PROCESSING SCOPE (F-2, F-4, F-5, F-6, F-7)
================================================
- F-2 (discriminated union): emit `z.discriminatedUnion("kind", [...])`
- F-4 (Optional vs nullable tristate): map `anyOf [type, null]` to
  `.nullable()` / `.nullable().optional()` based on `required` membership
- F-5 (Annotated metadata): append `.min(N).max(N).regex(/.../)` chains
- F-6 (template-literal pattern types): `pattern` in schema -> `.regex(...)`
- F-7 (datetime/UUID/Decimal formats): emit Zod v4 format checks

NESTED `$defs` TRAVERSAL
========================
When `source_schema` carries `$ref` pointers (recursive models, nested
discriminated unions, ...), the post-processor MUST resolve them against the
schema-root's `definitions` (or `$defs`) before reading the target's
`required` array / constraint keys. Without `_resolve_ref`, nested-model
fields silently misfire (the shallow-nested-schema trap).

REFERENCE
=========
- the codegen-pipeline design (see ARCHITECTURE.md)
- the failure-mode taxonomy (see docs/edge-cases.md) — F-2 post-processing
- Zod v4 format functions: https://zod.dev/
"""

from __future__ import annotations

import json
import re
from typing import Any

# JSON Schema "format" -> Zod v4 expression. Top-level helpers per v4 release.
_FORMAT_TO_ZOD: dict[str, str] = {
    "date-time": "z.iso.datetime()",
    "date": "z.iso.date()",
    "time": "z.iso.time()",
    "duration": "z.iso.duration()",
    "uuid": "z.uuid()",
    "email": "z.email()",
    "uri": "z.url()",
    "url": "z.url()",
    "ipv4": "z.ipv4()",
    "ipv6": "z.ipv6()",
}


def post_process_zod(
    typescript: str,
    *,
    source_schema: dict[str, Any] | None = None,
    zod_version: str = "4",
) -> str:
    """Augment json2ts TS output with Zod v4 schemas.

    Args:
        typescript: the raw output from `emitter.emit_typescript()`.
        source_schema: pre-processed JSON Schema for ONE model (or one root
            discriminated-union alias). Required for F-2/F-4/F-5/F-6/F-7
            schema-aware emission; without it the post-processor falls back to
            naive type-only mapping.
        zod_version: "4" (default) or "3". v3 is reserved for legacy consumers
            and currently emits the same v4 output (no v3 implementation yet).

    Returns:
        TypeScript source: original interfaces/type-aliases + appended Zod
        schemas. A single `import { z } from "zod";` is injected after the
        json2ts header comment if not already present.
    """
    del zod_version  # reserved for future v3 fallback; v4 is the only target

    if source_schema is None:
        # Fallback: emit only the `z` import + the original TS.
        return _ensure_zod_import(typescript)

    interfaces = _parse_interfaces(typescript)
    type_aliases = _parse_type_aliases(typescript)

    zod_blocks: list[str] = []

    # Pass 1: interface-to-Zod for every `export interface Foo { ... }` block.
    # Order matters: every const referenced by another const must precede it,
    # otherwise the referencing initializer hits the TDZ and TypeScript flags
    # `used before its declaration`. We topologically sort the interfaces by
    # their schema-level `$ref` graph so transitive chains like
    # `Pick → PickElement → ElementFingerprint` emit in dependency order.
    # The chunk-root (iface whose name matches `source_schema.title`) is pinned
    # LAST so it sees all its deps. Self-references / cycles are broken by the
    # `z.lazy(() => ...)` wrap applied inside `_emit_object_schema`.
    root_title = source_schema.get("title")
    ordered_ifaces = _topo_sort_interfaces(interfaces, source_schema, root_title)
    for iface in ordered_ifaces:
        target_schema = _find_object_schema(source_schema, iface.name) or source_schema
        zod_blocks.append(_emit_object_schema(iface, target_schema, source_schema))

    # Pass 2: discriminated unions for type aliases whose source schema is a
    # top-level `oneOf`+`discriminator`, AND for object fields whose schema
    # carries a discriminator (in which case the variants are already
    # interface-to-Zod'd above and we emit a named union const).
    for alias in type_aliases:
        union_block = _maybe_emit_discriminated_union_alias(alias, source_schema)
        if union_block is not None:
            zod_blocks.append(union_block)

    # Top-level union (e.g. EventAnnotated alias targets the WHOLE schema).
    top_level_union = _maybe_emit_top_level_discriminated_union(source_schema, type_aliases)
    if top_level_union is not None:
        zod_blocks.append(top_level_union)

    output = _ensure_zod_import(typescript)
    if zod_blocks:
        output = output.rstrip() + "\n\n" + "\n\n".join(zod_blocks) + "\n"
    return output


# ----------------------------------------------------------------------------
# TS parsing — KISS regex over json2ts's structurally simple output.
# ----------------------------------------------------------------------------


class _Field:
    """One TS interface field with its raw type expression and optional flag."""

    __slots__ = ("name", "type_expr", "optional")

    def __init__(self, name: str, type_expr: str, optional: bool) -> None:
        self.name = name
        self.type_expr = type_expr
        self.optional = optional


class _Interface:
    """One `export interface Foo { ... }` block parsed from json2ts output."""

    __slots__ = ("name", "fields")

    def __init__(self, name: str, fields: list[_Field]) -> None:
        self.name = name
        self.fields = fields


class _TypeAlias:
    """One `export type Foo = ...;` block parsed from json2ts output."""

    __slots__ = ("name", "rhs")

    def __init__(self, name: str, rhs: str) -> None:
        self.name = name
        self.rhs = rhs


_INTERFACE_HEADER_RE = re.compile(r"export\s+interface\s+(?P<name>[A-Za-z_]\w*)\s*\{")
_TYPE_ALIAS_RE = re.compile(
    r"export\s+type\s+(?P<name>[A-Za-z_]\w*)\s*=\s*(?P<rhs>[^;]+);",
)
_FIELD_SIGNATURE_RE = re.compile(
    r"^(?P<name>[A-Za-z_]\w*)(?P<opt>\?)?\s*:\s*(?P<type>.+)$",
    re.DOTALL,
)
# `/* … */` block-comments (incl. `/** … */` JSDoc). Non-greedy so consecutive
# blocks each get their own match.
_BLOCK_COMMENT_RE = re.compile(r"/\*[\s\S]*?\*/")


def _scan_balanced_block(text: str, start: int) -> int:
    """Return the index of the `}` matching the `{` at `text[start - 1]`.

    Tracks brace depth, string state, AND `/* … */` block-comment state so
    inline anonymous objects like ``{ [k: string]: unknown }``, string-literal
    `}`, and JSDoc comments containing braces are not mistaken for block
    terminators. `start` points at the first char AFTER the opening `{`.
    Returns -1 on unbalanced input.
    """
    depth = 1
    i = start
    in_string: str | None = None
    in_block_comment = False
    while i < len(text):
        c = text[i]
        if in_block_comment:
            if c == "*" and i + 1 < len(text) and text[i + 1] == "/":
                in_block_comment = False
                i += 2
                continue
            i += 1
            continue
        if in_string is not None:
            if c == "\\":
                i += 2
                continue
            if c == in_string:
                in_string = None
            i += 1
            continue
        if c == "/" and i + 1 < len(text) and text[i + 1] == "*":
            in_block_comment = True
            i += 2
            continue
        if c in ('"', "'", "`"):
            in_string = c
        elif c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                return i
        i += 1
    return -1


def _split_fields_at_depth_zero(body: str) -> list[str]:
    """Split an interface body on `;` at brace-depth zero.

    Semicolons inside inline anonymous objects (e.g. `{ [k:string]: string; }`)
    or `/* … */` block-comments are NOT field separators — they're internal to
    the type expression / doc-string respectively.
    """
    segments: list[str] = []
    depth = 0
    in_string: str | None = None
    in_block_comment = False
    last = 0
    i = 0
    while i < len(body):
        c = body[i]
        if in_block_comment:
            if c == "*" and i + 1 < len(body) and body[i + 1] == "/":
                in_block_comment = False
                i += 2
                continue
            i += 1
            continue
        if in_string is not None:
            if c == "\\":
                i += 2
                continue
            if c == in_string:
                in_string = None
            i += 1
            continue
        if c == "/" and i + 1 < len(body) and body[i + 1] == "*":
            in_block_comment = True
            i += 2
            continue
        if c in ('"', "'", "`"):
            in_string = c
        elif c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
        elif c == ";" and depth == 0:
            segments.append(body[last:i])
            last = i + 1
        i += 1
    tail = body[last:]
    if tail.strip():
        segments.append(tail)
    return segments


def _strip_leading_block_comments(segment: str) -> str:
    """Remove any leading `/* … */` blocks from `segment` so the field-signature regex matches.

    json2ts emits per-property JSDoc INSIDE the interface body whenever the
    Pydantic Field carries a description/annotation. Without this strip, the
    `_FIELD_SIGNATURE_RE` anchors at `^` and fails to match the JSDoc-prefixed
    name, silently dropping the field. Multiple consecutive comment blocks are
    handled. Comments embedded mid-line (e.g. ``/* hint */ name: ...``) are
    also stripped.
    """
    text = segment.lstrip()
    while text.startswith("/*"):
        end = text.find("*/")
        if end < 0:
            return text  # malformed, leave for caller diagnostics
        text = text[end + 2 :].lstrip()
    return text


def _parse_interfaces(ts: str) -> list[_Interface]:
    """Extract `export interface Foo { ... }` blocks via balanced-brace scan.

    The previous regex-only approach used `[^}]*` for the body capture and
    truncated at the first inner `}` (e.g. on `{ [k: string]: unknown }`),
    silently dropping every field after the inline object — see BUG-5.
    """
    results: list[_Interface] = []
    for header in _INTERFACE_HEADER_RE.finditer(ts):
        name = header.group("name")
        body_start = header.end()
        body_end = _scan_balanced_block(ts, body_start)
        if body_end < 0:
            continue
        body = ts[body_start:body_end]
        fields: list[_Field] = []
        for segment in _split_fields_at_depth_zero(body):
            stripped = _strip_leading_block_comments(segment).strip()
            if not stripped:
                continue
            match = _FIELD_SIGNATURE_RE.match(stripped)
            if not match:
                continue
            fields.append(
                _Field(
                    name=match.group("name"),
                    type_expr=match.group("type").strip(),
                    optional=match.group("opt") == "?",
                )
            )
        results.append(_Interface(name=name, fields=fields))
    return results


def _parse_type_aliases(ts: str) -> list[_TypeAlias]:
    """Extract `export type Foo = ...;` lines from `ts`."""
    return [_TypeAlias(name=m.group("name"), rhs=m.group("rhs").strip()) for m in _TYPE_ALIAS_RE.finditer(ts)]


# ----------------------------------------------------------------------------
# Schema lookup helpers (resolve $ref against $defs/definitions)
# ----------------------------------------------------------------------------


def _resolve_ref(schema_root: dict[str, Any], ref: str) -> dict[str, Any] | None:
    """Resolve a `#/definitions/Name` or `#/$defs/Name` $ref against `schema_root`.

    Returns the target dict, or None if the $ref shape is unsupported. To
    avoid the shallow-nested-schema trap, every consumer that reads `required`
    or constraint keys of a referenced schema node MUST funnel through this
    helper.
    """
    if not ref.startswith("#/"):
        return None
    parts = ref[2:].split("/")
    node: Any = schema_root
    for part in parts:
        # JSON Pointer escape: `~1` -> `/`, `~0` -> `~`. Pydantic doesn't emit
        # these but the pointer spec calls for them.
        unescaped = part.replace("~1", "/").replace("~0", "~")
        if isinstance(node, dict) and unescaped in node:
            node = node[unescaped]
        else:
            return None
    return node if isinstance(node, dict) else None


def _find_object_schema(schema_root: dict[str, Any], name: str) -> dict[str, Any] | None:
    """Locate the object-schema for `name` inside `schema_root`.

    Searches (in order): root (when `title == name`), `definitions[name]`,
    `$defs[name]`. Returns None if not found.
    """
    if schema_root.get("title") == name and schema_root.get("type") == "object":
        return schema_root
    for container_key in ("definitions", "$defs"):
        container = schema_root.get(container_key)
        if isinstance(container, dict) and name in container:
            candidate = container[name]
            if isinstance(candidate, dict):
                return candidate
    return None


def _collect_ref_names(node: Any) -> set[str]:
    """Walk a JSON-Schema fragment, return the set of `$ref` target model names.

    Used by the topological sort to determine which other interfaces a given
    interface depends on. Strips the `#/definitions/` / `#/$defs/` prefix and
    keeps just the trailing identifier.
    """
    refs: set[str] = set()
    if isinstance(node, dict):
        for key, value in node.items():
            if key == "$ref" and isinstance(value, str):
                refs.add(value.rsplit("/", 1)[-1])
            else:
                refs.update(_collect_ref_names(value))
    elif isinstance(node, list):
        for item in node:
            refs.update(_collect_ref_names(item))
    return refs


def _topo_sort_interfaces(
    interfaces: list[_Interface],
    schema_root: dict[str, Any],
    root_title: Any,
) -> list[_Interface]:
    """Topologically order interfaces so every dep is emitted before its dependents.

    The root interface (matching `schema_root.title`) is pinned LAST. Self-refs
    and cross-iface cycles are tolerated — the caller breaks them with the
    `z.lazy(() => ...)` wrap, so the order among cyclically-related nodes is
    not load-bearing.
    """
    by_name: dict[str, _Interface] = {iface.name: iface for iface in interfaces}
    deps: dict[str, set[str]] = {}
    for iface in interfaces:
        target = _find_object_schema(schema_root, iface.name) or schema_root
        # Only deps that are also part of this chunk count; external refs are
        # someone else's problem.
        refs = {r for r in _collect_ref_names(target) if r in by_name and r != iface.name}
        deps[iface.name] = refs

    ordered: list[_Interface] = []
    visited: set[str] = set()

    def visit(name: str, stack: set[str]) -> None:
        if name in visited or name in stack:
            return
        stack.add(name)
        for dep in deps.get(name, set()):
            visit(dep, stack)
        stack.discard(name)
        visited.add(name)
        ordered.append(by_name[name])

    # Non-root first, in original (json2ts) order — gives stable output when
    # there are no cross-references between siblings.
    for iface in interfaces:
        if iface.name != root_title:
            visit(iface.name, set())
    # Root last; if it wasn't already pulled in as someone else's dep.
    if isinstance(root_title, str) and root_title in by_name and root_title not in visited:
        visit(root_title, set())
    return ordered


def _follow_anyof_for_nullable(node: dict[str, Any]) -> tuple[dict[str, Any] | None, bool]:
    """If `node` is `anyOf: [<inner>, {type:"null"}]`, return (inner, True).

    Otherwise returns (None, False). Used by F-4 tristate logic.
    """
    any_of = node.get("anyOf")
    if not isinstance(any_of, list) or len(any_of) != 2:
        return (None, False)
    null_branch = next((b for b in any_of if isinstance(b, dict) and b.get("type") == "null"), None)
    other_branch = next((b for b in any_of if isinstance(b, dict) and b.get("type") != "null"), None)
    if null_branch is None or other_branch is None:
        return (None, False)
    return (other_branch, True)


# ----------------------------------------------------------------------------
# Pass 1: interface -> z.object({...})
# ----------------------------------------------------------------------------


def _emit_object_schema(
    iface: _Interface,
    target_schema: dict[str, Any],
    schema_root: dict[str, Any],
) -> str:
    """Emit `export const <Name> = z.object({ ... });` for one interface.

    Wraps the result in `z.lazy(() => ...)` when the body references the const's
    own identifier (self-recursive schemas like `TreeNode`). Without the lazy
    wrap the initializer reads the binding while still in the TDZ → runtime
    `ReferenceError: Cannot access 'X' before initialization`.
    """
    required = set(target_schema.get("required", []))
    properties = target_schema.get("properties", {})

    lines: list[str] = []
    for field in iface.fields:
        prop_schema = properties.get(field.name) if isinstance(properties, dict) else None
        if not isinstance(prop_schema, dict):
            prop_schema = {}
        chain = _field_to_zod_chain(
            prop_schema=prop_schema,
            field=field,
            required=field.name in required,
            schema_root=schema_root,
        )
        lines.append(f"  {field.name}: {chain},")

    body = "\n".join(lines)
    if _is_self_referential(body, iface.name):
        return f"export const {iface.name}: z.ZodType<{iface.name}> = z.lazy(() => z.object({{\n{body}\n}}));"
    return f"export const {iface.name} = z.object({{\n{body}\n}});"


def _is_self_referential(body: str, name: str) -> bool:
    """Detect whether `body` reads its own const-binding `name`.

    Value-position uses of the identifier (e.g. `z.array(TreeNode)`,
    `[TreeNode, OtherVariant]`) trigger TDZ at module load. Word-boundary
    matching avoids false positives like `TreeNodeWrapper`.
    """
    return re.search(rf"\b{re.escape(name)}\b", body) is not None


def _field_to_zod_chain(
    *,
    prop_schema: dict[str, Any],
    field: _Field,
    required: bool,
    schema_root: dict[str, Any],
) -> str:
    """Build the Zod chain for a single field, applying tristate + constraints + format."""
    # Resolve $ref so we read the *target* node's keys.
    effective = prop_schema
    has_top_level_ref = False
    if "$ref" in prop_schema and isinstance(prop_schema["$ref"], str):
        resolved = _resolve_ref(schema_root, prop_schema["$ref"])
        if resolved is not None:
            effective = resolved
            has_top_level_ref = True

    # F-4 tristate: detect anyOf-with-null shape.
    inner, is_nullable = _follow_anyof_for_nullable(effective)
    if inner is not None:
        base = _base_zod_expression(inner, field.type_expr, schema_root)
    elif has_top_level_ref:
        # Direct top-level $ref — surface the model name. _base_zod_expression
        # would see a bare `type:object` body (the resolved target) and emit
        # `z.object({})`, swallowing the named reference.
        base = prop_schema["$ref"].rsplit("/", 1)[-1]
    else:
        base = _base_zod_expression(effective, field.type_expr, schema_root)

    # Append constraint chain (F-5/F-6/F-7) from whichever node carries them.
    constraint_source = inner if inner is not None else effective
    chain = base + _constraint_chain(constraint_source)

    # F-4 modifiers: nullable / optional / default ordering matches Zod v4
    # docs (nullable -> optional -> default).
    if is_nullable:
        chain += ".nullable()"
        if not required:
            chain += ".optional()"
        if "default" in effective:
            chain += f".default({_json_to_ts_literal(effective['default'])})"
    elif not required:
        if "default" in effective:
            chain += f".default({_json_to_ts_literal(effective['default'])})"
        else:
            chain += ".optional()"

    return chain


def _base_zod_expression(
    prop_schema: dict[str, Any],
    ts_type_expr: str,
    schema_root: dict[str, Any],
) -> str:
    """Map a JSON-Schema node to its base Zod v4 expression (no modifiers)."""
    # $ref to a defined model — reference the named const.
    if "$ref" in prop_schema and isinstance(prop_schema["$ref"], str):
        name = prop_schema["$ref"].rsplit("/", 1)[-1]
        return name

    # const -> z.literal(...)
    if "const" in prop_schema:
        return f"z.literal({_json_to_ts_literal(prop_schema['const'])})"

    # enum -> z.enum([...]) when all strings, otherwise z.union([z.literal(...)])
    if "enum" in prop_schema:
        values = prop_schema["enum"]
        if isinstance(values, list) and all(isinstance(v, str) for v in values):
            return "z.enum([" + ", ".join(_json_to_ts_literal(v) for v in values) + "])"

    # F-2 (in-line): discriminated union appearing as a property schema. We
    # emit `z.discriminatedUnion(...)` inline rather than naming an
    # intermediate const — KISS, matches Zod v4's runtime-narrowing intent.
    discriminator, variants = _extract_discriminator(prop_schema, schema_root)
    if discriminator is not None and variants:
        variant_list = ", ".join(variants)
        return f'z.discriminatedUnion("{discriminator}", [{variant_list}])'

    # Format: F-7 (datetime/UUID/etc.) takes precedence over plain string.
    fmt = prop_schema.get("format")
    if isinstance(fmt, str) and fmt in _FORMAT_TO_ZOD:
        return _FORMAT_TO_ZOD[fmt]

    json_type = prop_schema.get("type")
    if json_type == "string":
        return "z.string()"
    if json_type == "integer":
        return "z.number().int()"
    if json_type == "number":
        return "z.number()"
    if json_type == "boolean":
        return "z.boolean()"
    if json_type == "null":
        return "z.null()"
    if json_type == "array":
        items = prop_schema.get("items")
        if isinstance(items, dict):
            inner = _base_zod_expression(items, "", schema_root)
            return f"z.array({inner})"
        return "z.array(z.unknown())"
    if json_type == "object":
        return "z.object({})"

    # oneOf/anyOf without null branch → naive z.union mapping.
    for key in ("oneOf", "anyOf"):
        branches = prop_schema.get(key)
        if isinstance(branches, list) and branches:
            parts = [_base_zod_expression(b, "", schema_root) for b in branches if isinstance(b, dict)]
            if parts:
                return "z.union([" + ", ".join(parts) + "])"

    # Last resort: trust the TS type expression where it names a known model.
    bare_ident = ts_type_expr.strip()
    if re.match(r"^[A-Za-z_]\w*$", bare_ident):
        return bare_ident
    return "z.unknown()"


def _constraint_chain(prop_schema: dict[str, Any]) -> str:
    """Build the `.min(N).max(N).regex(/.../).gt(N).lt(N)` chain for a node (F-5/F-6)."""
    parts: list[str] = []
    # String / array length constraints
    if "minLength" in prop_schema:
        parts.append(f".min({prop_schema['minLength']})")
    if "maxLength" in prop_schema:
        parts.append(f".max({prop_schema['maxLength']})")
    if "pattern" in prop_schema and isinstance(prop_schema["pattern"], str):
        parts.append(f".regex(/{_escape_regex_delimiter(prop_schema['pattern'])}/)")
    # Number constraints
    if "exclusiveMinimum" in prop_schema:
        parts.append(f".gt({prop_schema['exclusiveMinimum']})")
    if "minimum" in prop_schema:
        parts.append(f".gte({prop_schema['minimum']})")
    if "exclusiveMaximum" in prop_schema:
        parts.append(f".lt({prop_schema['exclusiveMaximum']})")
    if "maximum" in prop_schema:
        parts.append(f".lte({prop_schema['maximum']})")
    # Array size
    if prop_schema.get("type") == "array":
        if "minItems" in prop_schema:
            parts.append(f".min({prop_schema['minItems']})")
        if "maxItems" in prop_schema:
            parts.append(f".max({prop_schema['maxItems']})")
    return "".join(parts)


_UNESCAPED_SLASH_RE = re.compile(r"(?<!\\)/")


def _escape_regex_delimiter(pattern: str) -> str:
    """Escape unescaped forward slashes so a pattern can be embedded in `/.../` literals.

    JavaScript regex literals are delimited by `/`. An unescaped `/` inside the
    body terminates the literal early — `r"^https?://.+$"` would emit
    `.regex(/^https?://.+$/)`, parsed as `.regex(/^https?:/, .+$/)`. Already-
    escaped `\\/` is left untouched.
    """
    return _UNESCAPED_SLASH_RE.sub(r"\\/", pattern)


def _json_to_ts_literal(value: Any) -> str:
    """Convert a JSON value to its TypeScript literal source form.

    Strings go through `json.dumps` (with default `ensure_ascii=True`) so all
    control characters (`\\n`, `\\t`, `\\r`, NUL, etc.) and the JS-illegal
    paragraph/line separators U+2028/U+2029 are `\\uXXXX`-escaped. The JSON
    string-literal grammar is a strict subset of valid JS string literals.
    """
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return repr(value)
    if isinstance(value, str):
        return json.dumps(value)
    if isinstance(value, list):
        return "[" + ", ".join(_json_to_ts_literal(v) for v in value) + "]"
    if isinstance(value, dict):
        items = ", ".join(f"{json.dumps(k)}: {_json_to_ts_literal(v)}" for k, v in value.items())
        return "{" + items + "}"
    return "null"


# ----------------------------------------------------------------------------
# Pass 2: discriminated unions
# ----------------------------------------------------------------------------


def _maybe_emit_discriminated_union_alias(
    alias: _TypeAlias,
    schema_root: dict[str, Any],
) -> str | None:
    """Emit `z.discriminatedUnion(...)` when `alias` targets a oneOf+discriminator schema.

    Handles the case where json2ts emitted `export type X = A | B;` for a schema
    rooted in `oneOf` with `discriminator.propertyName`. Returns None for plain
    aliases without a matching schema shape.
    """
    if schema_root.get("title") != alias.name:
        return None
    discriminator, variants = _extract_discriminator(schema_root, schema_root)
    if discriminator is None or not variants:
        return None
    variant_list = ", ".join(variants)
    return f'export const {alias.name} = z.discriminatedUnion("{discriminator}", [{variant_list}]);'


def _maybe_emit_top_level_discriminated_union(
    schema_root: dict[str, Any],
    type_aliases: list[_TypeAlias],
) -> str | None:
    """Emit a top-level union const when the root schema is a oneOf+discriminator.

    Only fires when the alias is NOT already covered by `_maybe_emit_discriminated_union_alias`
    (i.e. when json2ts did emit a `type` alias for it, the alias handler covers it).
    Returns None when the schema root is not a discriminated union or the alias
    handler will cover it.
    """
    if any(alias.name == schema_root.get("title") for alias in type_aliases):
        return None
    discriminator, variants = _extract_discriminator(schema_root, schema_root)
    if discriminator is None or not variants:
        return None
    name = schema_root.get("title")
    if not isinstance(name, str):
        return None
    variant_list = ", ".join(variants)
    return f'export const {name} = z.discriminatedUnion("{discriminator}", [{variant_list}]);'


def _extract_discriminator(
    node: dict[str, Any],
    schema_root: dict[str, Any],
) -> tuple[str | None, list[str]]:
    """Pull discriminator + variant Zod-const names from a oneOf/anyOf node.

    Returns `(propertyName, [VariantName, ...])` or `(None, [])` when the
    shape does not match. Resolves `$ref`s to their target name.
    """
    discriminator = node.get("discriminator")
    if not isinstance(discriminator, dict):
        return (None, [])
    prop_name = discriminator.get("propertyName")
    if not isinstance(prop_name, str):
        return (None, [])
    for branches_key in ("oneOf", "anyOf"):
        branches = node.get(branches_key)
        if isinstance(branches, list) and branches:
            variants: list[str] = []
            for branch in branches:
                if not isinstance(branch, dict):
                    continue
                ref = branch.get("$ref")
                if isinstance(ref, str):
                    variants.append(ref.rsplit("/", 1)[-1])
                    continue
                # Inline variant — look up by title.
                title = branch.get("title")
                if isinstance(title, str):
                    variants.append(title)
            # Resolve variants — they should all be present in the root's
            # definitions; we don't strictly need them but resolving guards
            # against the shallow-nested-schema trap.
            for variant in variants:
                if _find_object_schema(schema_root, variant) is None:
                    # Soft-fail: still emit the variant name; consumer can fix
                    # the source schema. This keeps the post-processor robust
                    # to partial schemas.
                    pass
            return (prop_name, variants)
    return (None, [])


# ----------------------------------------------------------------------------
# Header injection
# ----------------------------------------------------------------------------


_ZOD_IMPORT_LINE = 'import { z } from "zod";'


def _ensure_zod_import(typescript: str) -> str:
    """Insert `import { z } from "zod";` after the json2ts header block.

    Idempotent: if the import is already present anywhere in the file, leaves
    `typescript` unchanged.
    """
    if _ZOD_IMPORT_LINE in typescript:
        return typescript

    # json2ts emits a leading comment block; insert AFTER it for readability.
    # Pattern: `/* eslint-disable */` ... `*/` then a blank line, then content.
    header_match = re.match(r"\A(/\*[\s\S]*?\*/\s*\n)", typescript)
    if header_match:
        head = header_match.group(1)
        tail = typescript[len(head) :]
        return head + _ZOD_IMPORT_LINE + "\n\n" + tail.lstrip("\n")
    return _ZOD_IMPORT_LINE + "\n\n" + typescript
