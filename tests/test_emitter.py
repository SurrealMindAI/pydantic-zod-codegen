"""Smoke tests for the bunx json2ts subprocess bridge."""

from __future__ import annotations

import shutil

import pytest

from pydantic_zod_codegen.emitter import doctor, emit_typescript

pytestmark = pytest.mark.skipif(
    shutil.which("bunx") is None,
    reason="bunx not on PATH — skipping emitter integration tests",
)


def test_doctor_returns_empty_list_on_healthy_env() -> None:
    """doctor() returns [] when bunx + json2ts are reachable."""
    diagnostics = doctor()
    assert diagnostics == [], f"unexpected diagnostics: {diagnostics}"


def test_emit_typescript_produces_interface_for_simple_schema() -> None:
    """emit_typescript wraps bunx json2ts; smoke-test against a tiny schema."""
    schema = {
        "title": "Greeting",
        "type": "object",
        "properties": {
            "message": {"type": "string"},
            "count": {"type": "integer"},
        },
        "required": ["message"],
        "additionalProperties": False,
    }
    ts = emit_typescript(schema, name_hint="Greeting", timeout_sec=60)
    assert "export interface Greeting" in ts
    assert "message: string" in ts
    assert "count?: number" in ts  # optional + integer maps to number?


def test_doctor_reports_missing_bunx() -> None:
    """When bunx is missing, doctor() returns at least one diagnostic line."""
    # This test only runs the negative branch when bunx is missing,
    # which is exactly when the module-level skipif fires — so we use
    # monkeypatch to simulate the missing-bunx case while bunx is present.
    import pydantic_zod_codegen.emitter as em

    original_which = em.shutil.which
    try:
        em.shutil.which = lambda _name: None  # type: ignore[assignment]
        diagnostics = doctor()
        assert diagnostics, "doctor() should report bunx-not-found when which() returns None"
        assert any("bunx" in d.lower() for d in diagnostics)
    finally:
        em.shutil.which = original_which  # type: ignore[assignment]
