"""CI drift test — protects the wire-protocol SSoT contract.

PURPOSE
=======
CI runs `pytest -m drift`. If the committed `.gen.ts` no longer equals what
the pipeline would emit today, this test fails with a unified diff. That
means somebody changed a Pydantic model but forgot to regenerate.

This is exactly the `regenerate -> git diff --quiet` discipline mandated by
the drift-gate design (see ARCHITECTURE.md).

LOCAL DEV WORKFLOW
==================
Fail locally? Run:

    pydantic-zod-codegen generate tests.goldenfile.fixtures.models \\
        -o tests/goldenfile/expected/protocol.gen.ts

then `git diff` to inspect the change.

REFERENCE
=========
- the drift-gate design (see ARCHITECTURE.md)
"""

from __future__ import annotations

from pathlib import Path

import pytest


@pytest.mark.drift
def test_committed_output_matches_pipeline(expected_dir: Path) -> None:
    """Drift gate: re-emit and compare to the committed file.

    This is intentionally the ONLY drift test. One protocol, one gate.
    """
    from pydantic_zod_codegen import check_drift

    drift_detected, diff_text = check_drift(
        "tests.goldenfile.fixtures.models",
        expected_dir / "protocol.gen.ts",
    )
    assert drift_detected is False, diff_text


# ============================================================================
# BUG-8: dedupe pass must not leak orphan content from interfaces with inline
# anonymous objects (e.g. `dict[str, str]` → `{ [k: string]: string }`).
# ============================================================================


def test_bug8_dedupe_interface_with_inline_object_no_orphan() -> None:
    """When a duplicate interface body contains an inline `{...}`, the entire body must be dropped.

    The non-greedy `[\\s\\S]*?\\}` in the dedupe regex stopped at the FIRST
    inner brace, leaving the rest of the body — and notably the terminating
    `;` after the inline object — as orphan content in the output. This
    showed up in a real-world pipeline as a stray `;` between the
    InspectorState interface block and the StateSnapshot const, breaking
    pretty-printing and risking confusing downstream tooling.
    """
    from pydantic_zod_codegen.drift import _dedupe_declarations

    # Chunk 1 + chunk 2 both materialise a `Fingerprint` interface whose body
    # contains an inline anonymous object (mimicking `dict[str, str]` from a
    # Pydantic Field). The dedupe pass MUST drop chunk 2's duplicate completely.
    chunk1 = (
        "export interface Fingerprint {\n"
        "  tag: string;\n"
        "  attributes?: {\n"
        "    [k: string]: string;\n"
        "  };\n"
        "  children?: string[];\n"
        "}\n"
    )
    chunk2_dup = (
        "export interface Fingerprint {\n"
        "  tag: string;\n"
        "  attributes?: {\n"
        "    [k: string]: string;\n"
        "  };\n"
        "  children?: string[];\n"
        "}\n"
        "\n"
        "export const Fingerprint = z.object({\n"
        "  tag: z.string(),\n"
        "});\n"
    )
    joined = chunk1 + "\n" + chunk2_dup
    out = _dedupe_declarations(joined)

    # The interface must appear exactly once.
    assert out.count("export interface Fingerprint") == 1, f"duplicate interface not deduped:\n{out}"
    # And the duplicate's removal must not leave behind stray syntax.
    assert "\n;\n" not in out, f"orphan `;` line leaked into deduped output:\n{out}"
    # The full original interface body of the kept declaration must remain intact
    # (specifically the `children?: string[];` field after the inline object).
    assert "children?: string[];" in out, f"first-occurrence body was truncated:\n{out}"


def test_bug8_dedupe_two_inline_objects_no_orphan() -> None:
    """An interface with TWO inline anonymous objects must still dedupe cleanly."""
    from pydantic_zod_codegen.drift import _dedupe_declarations

    iface = (
        "export interface Two {\n"
        "  a: {\n"
        "    [k: string]: string;\n"
        "  };\n"
        "  b: {\n"
        "    [k: string]: number;\n"
        "  };\n"
        "  c: boolean;\n"
        "}\n"
    )
    joined = iface + "\n" + iface
    out = _dedupe_declarations(joined)
    assert out.count("export interface Two") == 1
    assert "\n;\n" not in out, f"orphan semi:\n{out}"
    assert "c: boolean" in out
