"""TypeScript emitter — wraps `bunx json-schema-to-typescript@15` as a subprocess.

WHY THIS MODULE EXISTS
======================
We do NOT re-implement TS-emission. `json-schema-to-typescript@15` (json2ts) has
2.46M weekly downloads; the network effect is real. We invoke it via `bunx` so
no global node install is needed on developer machines.

The output is TS interfaces — NOT Zod schemas. The Zod augmentation happens in
post_processor.py (next stage in the pipeline).

WHY BUN/BUNX
============
- prefer bun > node — no global node install required
- bunx caches packages aggressively; first invocation slow, subsequent fast
- single binary, no PATH gymnastics
- runtime prereq is documented in README.md

REFERENCE
=========
- the codegen-pipeline design (see ARCHITECTURE.md) — tool choice
- the failure-mode taxonomy (see docs/edge-cases.md) — F-2 row 1
"""

from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any


def emit_typescript(
    schema: dict[str, Any],
    *,
    name_hint: str | None = None,
    json2ts_version: str = "15",
    timeout_sec: int = 60,
) -> str:
    """Run `bunx --bun json-schema-to-typescript@<version>` on the given schema dict.

    Args:
        schema: a pre-processed JSON Schema (post pre_processor.normalise_for_json2ts).
        name_hint: optional top-level interface name. Injected as schema "title"
            only when the schema does not already carry a title (no clobber).
        json2ts_version: npm major-version pin (default: "15").
        timeout_sec: subprocess kill timeout.

    Returns:
        the raw TypeScript source as emitted by json2ts (not yet Zod-augmented).

    Raises:
        RuntimeError: if `bunx` is not on PATH, json2ts crashes, or timeout fires.
    """
    if shutil.which("bunx") is None:
        raise RuntimeError(
            "bunx not found on PATH. Install bun via `brew install bun` or `curl -fsSL https://bun.sh/install | bash`."
        )

    payload = dict(schema)
    if name_hint is not None and "title" not in payload:
        payload["title"] = name_hint

    with tempfile.TemporaryDirectory() as tmp:
        in_path = Path(tmp) / "schema.json"
        in_path.write_text(json.dumps(payload), encoding="utf-8")
        try:
            result = subprocess.run(
                [
                    "bunx",
                    "--bun",
                    f"json-schema-to-typescript@{json2ts_version}",
                    str(in_path),
                ],
                capture_output=True,
                text=True,
                timeout=timeout_sec,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError(f"json2ts timed out after {timeout_sec}s") from exc

    if result.returncode != 0:
        raise RuntimeError(f"bunx json2ts failed (returncode={result.returncode}): {result.stderr.strip()}")
    return result.stdout


def doctor() -> list[str]:
    """Smoke-check the emitter's runtime prerequisites. Empty list = healthy.

    Returns a list of human-readable diagnostic lines. Never raises — callers
    can treat an empty list as "healthy" and a non-empty list as "report and
    optionally degrade".
    """
    if shutil.which("bunx") is None:
        return ["bunx not found on PATH — install bun via brew install bun or https://bun.sh/install"]
    try:
        result = subprocess.run(
            ["bunx", "--version"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except (subprocess.TimeoutExpired, OSError) as exc:
        return [f"bunx invocation failed: {exc}"]
    if result.returncode != 0:
        return [f"bunx --version returned {result.returncode}: {result.stderr.strip()}"]
    return []
