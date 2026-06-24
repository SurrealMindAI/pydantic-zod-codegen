"""pydantic-zod-codegen — Pydantic v2.13 -> JSON-Schema -> Zod v4 codegen pipeline.

Public API surface (skeleton — see ARCHITECTURE.md for the pipeline overview):

    from pydantic_zod_codegen import (
        CustomGenerateJsonSchema,   # Pydantic-side normalizing schema generator
        normalise_for_json2ts,      # $defs -> definitions string rewrite
        emit_typescript,            # bunx json2ts wrapper
        post_process_zod,           # Zod v4 brand/union/JSDoc augmentation
        check_drift,                # regenerate -> git diff --quiet helper
    )

Hand-rolled emitter escape hatch (for Generic[T] heavy schemas, ~150 LoC reserve):

    from pydantic_zod_codegen.hand_emitter import emit_handrolled

See `docs/edge-cases.md` for the seven failure classes (F-1..F-7) and which
component owns each.
"""

from __future__ import annotations

__version__ = "0.1.0"

# Re-export skeleton symbols. Each module currently raises NotImplementedError
# at call sites that depend on the full pipeline; see TODO markers per file.
from pydantic_zod_codegen.custom_schema import CustomGenerateJsonSchema
from pydantic_zod_codegen.drift import check_drift
from pydantic_zod_codegen.emitter import emit_typescript
from pydantic_zod_codegen.post_processor import post_process_zod
from pydantic_zod_codegen.pre_processor import normalise_for_json2ts

__all__ = [
    "CustomGenerateJsonSchema",
    "check_drift",
    "emit_typescript",
    "normalise_for_json2ts",
    "post_process_zod",
]
