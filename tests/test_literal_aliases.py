"""Tests for top-level ``Literal[...]`` type-alias emission.

WHY THIS TEST EXISTS
====================
A top-level Pydantic ``Foo = Literal["a", "b", "c"]`` alias that appears in a
module's ``__codegen_roots__`` (or ``__all__``) is the SSoT for that string
union — frontend code should consume it as a TypeScript string-literal-union
alias::

    export type Foo = "a" | "b" | "c";

Before the literal-alias-discovery patch, ``drift._discover_models`` only
recognised ``BaseModel`` subclasses and ``Annotated[Union, Discriminator(...)]``
aliases, so the Literal was silently skipped and downstream code had to do
``type Foo = SomeModel['fieldUsingFoo']`` indirection — a textbook SSoT
violation.

The drift gate (``test_drift.py::test_committed_output_matches_pipeline``)
covers the goldenfile-level assertion. These tests target the unit-level
contract for discovery + emission so failure modes are pinpointed.
"""

from __future__ import annotations

import importlib
from pathlib import Path
from types import ModuleType
from typing import Literal


def _build_literal_module(name: str, members: tuple[str, ...]) -> ModuleType:
    """Construct an in-memory module exposing a Literal alias in ``__codegen_roots__``.

    Built dynamically (instead of a committed fixture file) so the test owns
    its inputs end-to-end without coupling to ``tests/goldenfile/fixtures/`` —
    that module is the goldenfile's SSoT and adding members there would force
    a goldenfile regenerate as a side effect.
    """
    module = ModuleType(name)
    alias = Literal[members]  # type: ignore[valid-type]
    module.LiteralAlias = alias  # type: ignore[attr-defined]
    module.__codegen_roots__ = ["LiteralAlias"]  # type: ignore[attr-defined]
    module.__all__ = ["LiteralAlias"]  # type: ignore[attr-defined]
    import sys

    sys.modules[name] = module
    return module


def test_discover_models_yields_literal_alias_spec() -> None:
    """``_discover_models`` must return a spec for a Literal alias in __codegen_roots__."""
    from pydantic_zod_codegen.drift import _discover_models

    module = _build_literal_module("tests._tmp_literal_discover", ("relates_to", "triggers", "part_of"))
    specs = _discover_models(module)
    assert len(specs) == 1, f"expected 1 spec, got {len(specs)}: {specs!r}"
    name, schema = specs[0]
    assert name == "LiteralAlias"
    # The discovered schema should encode the Literal as a string-enum-like dict
    # — at minimum the three member strings must be reachable from the spec so
    # the emission path has the data it needs.
    flat = repr(schema)
    for member in ("relates_to", "triggers", "part_of"):
        assert member in flat, f"member {member!r} missing from schema spec: {schema!r}"


def test_regenerate_emits_top_level_type_alias_for_literal() -> None:
    """End-to-end: a top-level Literal must emit ``export type X = "..." | "...";``."""
    from pydantic_zod_codegen import drift as drift_mod

    _build_literal_module("tests._tmp_literal_emit", ("relates_to", "triggers", "part_of"))

    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        out_path = Path(tmp) / "out.gen.ts"
        body = drift_mod.regenerate("tests._tmp_literal_emit", out_path)

    expected_line = 'export type LiteralAlias = "relates_to" | "triggers" | "part_of";'
    assert expected_line in body, f"expected literal-alias emission missing from output:\n---\n{body}\n---"


def test_regenerate_emits_literal_alias_without_json2ts_pipeline() -> None:
    """Literal aliases must NOT route through bunx/json2ts (they don't parse bare literals).

    This is a behavioural guard: the implementation should emit the TS alias
    string directly. We assert by running the pipeline WITHOUT bunx on PATH
    (simulated by monkey-patching ``shutil.which`` inside the emitter module)
    and expecting success — proving the literal path is independent of json2ts.
    """
    import pydantic_zod_codegen.emitter as emitter_mod
    from pydantic_zod_codegen import drift as drift_mod

    _build_literal_module("tests._tmp_literal_no_bunx", ("alpha", "beta"))

    original_which = emitter_mod.shutil.which
    emitter_mod.shutil.which = lambda _name: None  # type: ignore[assignment]
    try:
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            out_path = Path(tmp) / "out.gen.ts"
            body = drift_mod.regenerate("tests._tmp_literal_no_bunx", out_path)
    finally:
        emitter_mod.shutil.which = original_which  # type: ignore[assignment]

    assert 'export type LiteralAlias = "alpha" | "beta";' in body


def test_literal_member_with_quote_is_json_escaped() -> None:
    """Members containing a double-quote must be JSON-escaped in the emitted alias.

    Belt-and-braces test: ensures the implementation uses ``json.dumps`` (or
    equivalent) rather than naive f-string interpolation, so future fixture
    additions with edge-case characters don't break the TS parser.
    """
    from pydantic_zod_codegen import drift as drift_mod

    _build_literal_module("tests._tmp_literal_escape", ('quoted"value', "plain"))

    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        out_path = Path(tmp) / "out.gen.ts"
        body = drift_mod.regenerate("tests._tmp_literal_escape", out_path)

    # `json.dumps("quoted\"value")` -> `"quoted\"value"`
    assert 'export type LiteralAlias = "quoted\\"value" | "plain";' in body, (
        f"escape handling broken:\n---\n{body}\n---"
    )


def test_goldenfile_fixture_module_imports_cleanly() -> None:
    """Sanity check: the goldenfile fixture module is importable (no syntax errors).

    Pinned so adding a Literal alias to ``tests/goldenfile/fixtures/models.py``
    doesn't accidentally break ``__codegen_roots__`` ordering or imports.
    """
    module = importlib.import_module("tests.goldenfile.fixtures.models")
    assert hasattr(module, "__codegen_roots__")
    assert hasattr(module, "__all__")
