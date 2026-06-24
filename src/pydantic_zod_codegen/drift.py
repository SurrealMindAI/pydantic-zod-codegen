"""Drift-test helper — re-generates Zod output and fails on git-diff drift.

WHY THIS MODULE EXISTS
======================
The drift-gate design (see ARCHITECTURE.md) mandates a `regenerate -> git diff --quiet`
CI check. The committed Zod schemas MUST equal the regenerated output.
Otherwise the wire protocol has silently drifted from the Pydantic SSoT.

This file is the canonical helper to wire that into CI:

    1. CI calls `pydantic-zod-codegen check-drift <models_module> <output_path>`
    2. We regenerate the output to a temp location
    3. Compare byte-for-byte with the committed file
    4. Non-empty diff -> exit non-zero with a readable diff

LOCAL WORKFLOW
==============
Same helper drives the developer-side regenerate command:

    pydantic-zod-codegen generate <models_module> -o frontend/src/lib/protocol.gen.ts

Drift = the developer forgot to regenerate after a Pydantic change.

PIPELINE COMPOSITION (per ARCHITECTURE.md §2)
=============================================
    for each model in module.__all__:
        schema = model.model_json_schema(schema_generator=CustomGenerateJsonSchema)
                 (or TypeAdapter(model).json_schema(...) for Annotated aliases)
        normalised = normalise_for_json2ts(schema)
        force_title(normalised, name)            # WORKAROUND 3
        promoted   = _promote_root_ref(normalised)  # WORKAROUND 1
        ts         = emit_typescript(promoted, name_hint=name)
        zod_chunk  = post_process_zod(ts, source_schema=normalised)
    -> concatenate, dedupe `import { z }` (WORKAROUND 4)
    -> first-occurrence-wins dedupe of `export interface/const/type` (WORKAROUND 2)
    -> single-file output

WORKAROUNDS (canonicalised from scripts/generate_goldenfile.py)
================================================================
1. Top-level `$ref` promotion + self-ref rewrite
   Pydantic emits recursive models as `{"$ref": "#/definitions/X", "definitions": {...}}`.
   json2ts@15 either crashes ("Refs should have been resolved by the resolver") or
   emits a duplicate `X1` interface. We inline the referenced definition at the root,
   rewrite internal `$ref: #/definitions/X` to `$ref: #` (root self-ref), and drop the
   duplicated entry from `definitions`.

2. Declaration dedupe pass
   Both `EventEnvelope` and `EventAnnotated` materialise `ClickEvent` + `KeyboardEvent`
   interfaces/consts. First-occurrence-wins regex pass strips duplicate `export
   interface`/`export const`/`export type` blocks from the concatenated output (post
   per-model post_process).

3. Force `title` on TypeAdapter-rooted schemas
   `TypeAdapter(EventAnnotated).json_schema(...)` produces a JSON Schema with
   `title=None`. Inject `title=name` before post_process_zod so the
   discriminated-union-alias matcher fires.

4. Single `import { z } from "zod";`
   Each per-model `post_process_zod` emits the import. Dedupe to exactly one at the
   top of the final concatenated output.

REFERENCE
=========
- the drift-gate design (see ARCHITECTURE.md)
- ARCHITECTURE.md §2 (pipeline) and §7 (drift contract)
"""

from __future__ import annotations

import difflib
import importlib
import json
import logging
import re
import tempfile
from copy import deepcopy
from pathlib import Path
from typing import Annotated, Any, Literal, get_args, get_origin

from pydantic import BaseModel, TypeAdapter

from pydantic_zod_codegen.custom_schema import CustomGenerateJsonSchema
from pydantic_zod_codegen.emitter import emit_typescript
from pydantic_zod_codegen.post_processor import _scan_balanced_block, post_process_zod
from pydantic_zod_codegen.pre_processor import normalise_for_json2ts

logger = logging.getLogger(__name__)


#: Sentinel key embedded in the discovery-spec dict to mark a top-level
#: ``Literal[...]`` type alias. The discovery layer is the only producer; the
#: pipeline composer in ``regenerate`` is the only consumer. Kept as a private
#: convention so the public ``check_drift`` signature stays a plain
#: ``list[tuple[str, dict]]``.
_LITERAL_ALIAS_MARKER = "__codegen_literal_alias__"


# ----------------------------------------------------------------------------
# Public API
# ----------------------------------------------------------------------------


def regenerate(models_module: str, output_path: Path) -> str:
    """Run the full pipeline and write the regenerated TypeScript+Zod source to disk.

    Args:
        models_module: dotted import path of a module that exposes a registry of
            Pydantic models (and `Annotated` discriminated-union aliases) in its
            ``__all__`` list (e.g. ``"myapp.protocol"``).
        output_path: where to write the generated ``.gen.ts`` file. Parent
            directories are created on demand.

    Returns:
        the generated TypeScript source (same as what was written to disk).
    """
    module = importlib.import_module(models_module)
    fixture_specs = _discover_models(module)

    chunks: list[str] = []
    for name, schema in fixture_specs:
        if schema.get(_LITERAL_ALIAS_MARKER):
            chunks.append(_emit_literal_alias(name, schema["members"]).strip())
            continue
        chunks.append(_strip_per_chunk_headers(_run_one_model(name, schema)).strip())

    deduped = _dedupe_declarations("\n\n".join(chunks))
    body = (
        "/* eslint-disable */\n"
        "/* AUTO-GENERATED — pydantic-zod-codegen pipeline output. DO NOT HAND-EDIT. */\n"
        'import { z } from "zod";\n\n' + deduped.rstrip() + "\n"
    )

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(body, encoding="utf-8")
    return body


def check_drift(models_module: str, committed_path: Path) -> tuple[bool, str]:
    """Regenerate and diff against the committed file.

    Args:
        models_module: see ``regenerate``.
        committed_path: path to the committed ``.gen.ts`` file.

    Returns:
        ``(drift_detected, diff_text)``. ``drift_detected`` is ``True`` iff
        ``diff_text`` is non-empty.
    """
    with tempfile.NamedTemporaryFile(suffix=".ts", delete=False) as tmp_handle:
        tmp_path = Path(tmp_handle.name)
    try:
        regenerate(models_module, tmp_path)
        actual = tmp_path.read_text(encoding="utf-8").splitlines(keepends=True)
        expected = committed_path.read_text(encoding="utf-8").splitlines(keepends=True)
        diff_lines = list(
            difflib.unified_diff(
                expected,
                actual,
                fromfile=str(committed_path),
                tofile="<regenerated>",
            )
        )
        diff_text = "".join(diff_lines)
        return (bool(diff_lines), diff_text)
    finally:
        tmp_path.unlink(missing_ok=True)


# ----------------------------------------------------------------------------
# Two-path model discovery (BaseModel vs Annotated alias)
# ----------------------------------------------------------------------------


def _discover_models(module: Any) -> list[tuple[str, dict[str, Any]]]:
    """Iterate the module's codegen-root list and classify entries into JSON Schemas.

    Discovery order:
    1. ``module.__codegen_roots__`` (preferred) — the curated, ordered list of
       TOP-LEVEL models. Lets the fixture exclude variants that are inlined into
       their parents (e.g. ``ClickEvent`` is part of ``EventEnvelope``'s schema)
       and types that are RESERVED for the dormant hand-emitter (F-3 generics).
    2. ``module.__all__`` (fallback) — every public symbol.

    BaseModel subclasses use ``model_json_schema(schema_generator=...)``. Annotated
    aliases (e.g. ``Annotated[Union[A, B], Discriminator("kind")]``) cannot — they
    are not classes — so we route them through ``TypeAdapter(...).json_schema(...)``.
    Anything else is skipped with a debug log line (functions, value constants,
    NamedTuples, etc.).
    """
    names = getattr(module, "__codegen_roots__", None) or getattr(module, "__all__", None)
    if names is None:
        raise RuntimeError(
            f"module {module.__name__!r} declares neither __codegen_roots__ nor __all__; "
            "cannot deterministically order codegen output."
        )

    specs: list[tuple[str, dict[str, Any]]] = []
    for name in names:
        if not hasattr(module, name):
            logger.debug("skip %s: not present on module", name)
            continue
        obj = getattr(module, name)

        if isinstance(obj, type) and issubclass(obj, BaseModel):
            try:
                schema = obj.model_json_schema(schema_generator=CustomGenerateJsonSchema)
            except Exception as exc:  # pragma: no cover - defensive
                logger.debug("skip %s: model_json_schema failed: %s", name, exc)
                continue
            specs.append((name, schema))
            continue

        if get_origin(obj) is Annotated:
            try:
                schema = TypeAdapter(obj).json_schema(schema_generator=CustomGenerateJsonSchema)
            except Exception as exc:  # pragma: no cover - defensive
                logger.debug("skip %s: TypeAdapter.json_schema failed: %s", name, exc)
                continue
            specs.append((name, schema))
            continue

        if get_origin(obj) is Literal:
            # Top-level `Foo = Literal["a", "b"]` alias. We don't route this
            # through json2ts — bare `{"enum": [...]}` schemas don't carry the
            # original alias name and the emitted TS would be anonymous. The
            # name lives in `__codegen_roots__`/`__all__`, the values live in
            # `typing.get_args(obj)`; we tag a marker dict so `regenerate`
            # takes the direct-emission path.
            members = list(get_args(obj))
            specs.append((name, {_LITERAL_ALIAS_MARKER: True, "members": members}))
            continue

        logger.debug("skip %s: not a BaseModel subclass, Annotated alias, or Literal alias", name)

    return specs


def _emit_literal_alias(name: str, members: list[Any]) -> str:
    """Render a top-level Literal alias as a self-contained TS string-union chunk.

    Goes straight to text — bypassing the bunx/json2ts subprocess and the
    Zod-augmentation post-processor — because:

    * json2ts cannot ingest a bare ``Literal[...]`` (no enclosing object,
      no title hook).
    * The Pydantic Literal IS the SSoT — no Zod runtime check beyond
      ``z.union(...)`` adds value, and the post-processor's
      discriminated-union machinery would mis-match.

    Members are serialised via ``json.dumps`` so edge-case characters
    (embedded quotes, unicode escapes) survive into the TS source.
    """
    pipe_separated = " | ".join(json.dumps(member) for member in members)
    type_alias = f"export type {name} = {pipe_separated};"
    zod_const = f"export const {name} = z.union([{', '.join(f'z.literal({json.dumps(m)})' for m in members)}]);"
    return f"{type_alias}\n\n{zod_const}\n"


# ----------------------------------------------------------------------------
# Per-model pipeline composition
# ----------------------------------------------------------------------------


def _run_one_model(name: str, schema: dict[str, Any]) -> str:
    """Run the full pipeline against one Pydantic-produced JSON Schema.

    Per sub-plan 03: post_process_zod sees the SAME per-model normalised schema
    that emit_typescript receives. The `_promote_root_ref` workaround mutates the
    shape json2ts ingests, but post_process_zod reads the unpromoted normalised
    form — that's what carries the original `definitions` block that the
    discriminated-union variant lookup relies on.
    """
    normalised = normalise_for_json2ts(schema)
    # WORKAROUND 3: force a title on TypeAdapter-rooted schemas so the
    # post-processor's discriminated-union-alias matcher (which keys off
    # `schema.title == alias.name`) fires.
    if "title" not in normalised:
        normalised["title"] = name
    promoted = _promote_root_ref(normalised)
    ts = emit_typescript(promoted, name_hint=name)
    return post_process_zod(ts, source_schema=normalised)


# ----------------------------------------------------------------------------
# WORKAROUND 1: top-level $ref promotion + self-ref rewrite (F-1 recursive)
# ----------------------------------------------------------------------------


def _promote_root_ref(schema: dict[str, Any]) -> dict[str, Any]:
    """Inline a top-level ``$ref`` so json2ts@15 can parse the schema.

    Pydantic emits recursive models as ``{"$ref": "#/definitions/X",
    "definitions": {...}}``. json2ts crashes on that exact shape
    (``Refs should have been resolved by the resolver``). Workaround:

    1. Copy ``definitions[X]`` to root (preserve all other keys for self-ref
       targets).
    2. Rewrite every internal ``$ref: #/definitions/X`` to ``$ref: #`` so json2ts
       sees a single recursive type (otherwise it emits a duplicate ``<X>1``
       interface).
    3. Drop the now-redundant ``definitions[X]`` entry; keep other definitions.
    """
    if "$ref" not in schema or not isinstance(schema["$ref"], str):
        return schema
    ref = schema["$ref"]
    if not ref.startswith("#/definitions/"):
        return schema
    name = ref.rsplit("/", 1)[-1]
    defs = schema.get("definitions", {})
    if not (isinstance(defs, dict) and name in defs):
        return schema

    promoted: dict[str, Any] = deepcopy(defs[name])
    remaining_defs = {k: v for k, v in defs.items() if k != name}
    _rewrite_ref_target(promoted, target=f"#/definitions/{name}", replacement="#")
    if remaining_defs:
        _rewrite_ref_target(remaining_defs, target=f"#/definitions/{name}", replacement="#")
        promoted["definitions"] = remaining_defs
    return promoted


def _rewrite_ref_target(node: Any, *, target: str, replacement: str) -> None:
    """In-place rewrite of any ``$ref`` value equal to ``target`` -> ``replacement``."""
    if isinstance(node, dict):
        for key, value in node.items():
            if key == "$ref" and value == target:
                node[key] = replacement
            else:
                _rewrite_ref_target(value, target=target, replacement=replacement)
    elif isinstance(node, list):
        for item in node:
            _rewrite_ref_target(item, target=target, replacement=replacement)


# ----------------------------------------------------------------------------
# WORKAROUND 4: strip per-chunk headers (json2ts banner + eslint-disable + zod import)
# ----------------------------------------------------------------------------


_JSON2TS_HEADER_RE = re.compile(
    r"/\*[\s\S]*?This file was automatically generated by json-schema-to-typescript[\s\S]*?\*/\s*\n?",
)


def _strip_per_chunk_headers(zod_chunk: str) -> str:
    """Remove per-chunk ``import { z }``, leading ``/* eslint-disable */``, and json2ts header.

    The final concatenated output keeps exactly ONE of each at the top via
    ``regenerate``.
    """
    out = zod_chunk
    out = re.sub(r'^import \{ z \} from "zod";\s*\n?', "", out, flags=re.MULTILINE)
    out = re.sub(r"^/\* eslint-disable \*/\s*\n?", "", out, flags=re.MULTILINE)
    out = _JSON2TS_HEADER_RE.sub("", out)
    return out


# ----------------------------------------------------------------------------
# WORKAROUND 2: first-occurrence-wins declaration dedupe
# ----------------------------------------------------------------------------


#: Header of a single top-level export declaration. The body is NOT captured by
#: regex — interfaces use balanced-brace scanning (so inline anonymous objects
#: like ``{ [k: string]: string }`` don't truncate the match), consts/type
#: aliases use a depth-0 ``;`` scan.
_DECL_HEADER_RE = re.compile(
    r"(?:(?P<jsdoc>/\*\*[\s\S]*?\*/\s*\n))?"  # optional JSDoc above
    r"export\s+"
    r"(?:(?P<iface>interface\s+(?P<iname>[A-Za-z_]\w*)\s*\{)"
    r"|(?P<const>const\s+(?P<cname>[A-Za-z_]\w*)\b)"
    r"|(?P<typ>type\s+(?P<tname>[A-Za-z_]\w*)\b))",
)


def _scan_to_depth_zero_semicolon(text: str, start: int) -> int:
    """Return the index of the first depth-0 `;` at or after `start`.

    Tracks brace depth, string state, and `/* … */` block-comments so semis
    inside any of those are skipped. Returns -1 on unbalanced / no-terminator
    input.
    """
    depth = 0
    in_string: str | None = None
    in_block_comment = False
    i = start
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
        elif c == ";" and depth == 0:
            return i
        i += 1
    return -1


def _dedupe_declarations(ts_body: str) -> str:
    """Strip duplicate top-level ``export`` blocks.

    Two fixtures (EventEnvelope + EventAnnotated) both materialise
    ``ClickEvent`` / ``KeyboardEvent`` — without this pass we'd emit each
    declaration twice. First-occurrence wins; subsequent duplicates are dropped
    along with the preceding JSDoc-comment block (if any) and trailing blank line.

    Interface bodies are scanned with balanced-brace counting so inline
    anonymous objects (e.g. ``attributes?: { [k: string]: string }`` for a
    Pydantic ``dict[str, str]`` field) don't truncate the match — the
    previous regex-only approach stopped at the FIRST inner ``}`` and left
    the rest of the body as orphan content when the duplicate was dropped.
    """
    seen: set[str] = set()
    out: list[str] = []
    last_end = 0
    pos = 0
    while pos < len(ts_body):
        match = _DECL_HEADER_RE.search(ts_body, pos)
        if match is None:
            break

        if match.group("iface"):
            # Interface — balanced-brace scan from the position right after `{`.
            body_end = _scan_balanced_block(ts_body, match.end())
            if body_end < 0:
                # Malformed input; keep the remainder as-is.
                break
            decl_end = body_end + 1
            name = match.group("iname")
            kind = "interface"
        elif match.group("const"):
            # Const — depth-0 `;` scan from the position right after the name.
            decl_end = _scan_to_depth_zero_semicolon(ts_body, match.end())
            if decl_end < 0:
                break
            decl_end += 1
            name = match.group("cname")
            kind = "const"
        else:
            # Type alias — depth-0 `;` scan (type bodies have no `{}` blocks
            # in our output, but the helper handles them safely).
            decl_end = _scan_to_depth_zero_semicolon(ts_body, match.end())
            if decl_end < 0:
                break
            decl_end += 1
            name = match.group("tname")
            kind = "type"

        key = f"{kind}:{name}"
        out.append(ts_body[last_end : match.start()])
        if key in seen:
            last_end = decl_end
            if last_end < len(ts_body) and ts_body[last_end] == "\n":
                last_end += 1
        else:
            seen.add(key)
            out.append(ts_body[match.start() : decl_end])
            last_end = decl_end
        pos = decl_end

    out.append(ts_body[last_end:])
    # Collapse 3+ consecutive newlines to clean blank-line separators.
    return re.sub(r"\n{3,}", "\n\n", "".join(out))
