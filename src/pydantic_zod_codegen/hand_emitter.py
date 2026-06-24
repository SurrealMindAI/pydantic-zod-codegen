"""Hand-rolled emitter escape hatch — for cases where json2ts loses information.

WHY THIS MODULE EXISTS — READ FIRST
====================================
This is the **documented reserve**, not the primary code path. The hybrid
json2ts pipeline (custom_schema -> pre_processor -> emitter -> post_processor)
handles 4 of 5 edge cases. ONE edge case it cannot handle:

    F-3: Generic[T] preservation. `Page[Item]` and `Page[User]` both flatten to
    concrete types in JSON-Schema. The generic relationship is LOST. Only a
    Python-side introspection emitter (walking `__pydantic_generic_metadata__`)
    can reconstruct `interface Page<T> { items: T[]; total: number }`.

WHEN TO ACTIVATE THIS MODULE
============================
Per the escape-valve table (see docs/edge-cases.md):

| Trigger | Why hand-roll |
|---------|---------------|
| 5+ Generic[T] envelope types in protocol | Page_Item_/Result_Foo_ noise kills review |
| Annotated metadata semantically load-bearing on the wire | json2ts drops min/max/pattern -> runtime drift risk |
| Pydantic v3 lands and breaks current pipeline | Hand-rolled is decoupled from model_json_schema() evolution |

Until a trigger fires, this module stays at ~150 LoC of reference prototype +
TODOs. It SHOULD NOT bloat into a competing emitter.

CO-EXISTENCE STRATEGY
=====================
Two-pass hybrid (overview.md §5):

    1. Mark a Pydantic model with `model_config = ConfigDict(
           json_schema_extra={"x-handrolled": True}
       )`
    2. The pipeline routes those models here; rest go through json2ts
    3. Main `.gen.ts` re-exports both: `export * from './handrolled.gen.ts'`

REFERENCE PROTOTYPE
===================
- the hand-rolled emitter prototype (see docs/edge-cases.md)
  (working prototype: Tests 1, 2, 5 from the edge-case matrix — Generic[T] left
  as the production-quality TODO).

TODO (implementer — DO NOT IMPLEMENT UNTIL A TRIGGER FIRES)
============================================================
- [ ] Port the prototype emitter into this file (~150 LoC ceiling)
- [ ] Add Generic[T] support — walk `cls.__pydantic_generic_metadata__`
- [ ] Add Zod-output mode (prototype emits TS only)
- [ ] Routing: skip models without `x-handrolled` marker
- [ ] Goldenfile tests — one fixture per supported edge case
"""

from __future__ import annotations

from typing import Any


def emit_handrolled(model_cls: type[Any]) -> str:
    """Emit TypeScript + Zod for a single Pydantic model via Python introspection.

    Args:
        model_cls: a Pydantic `BaseModel` subclass, optionally marked with
            `json_schema_extra={"x-handrolled": True}`.

    Returns:
        a self-contained TS source block (interface + Zod schema) to be
        concatenated into the project's `.gen.ts`.

    TODO: implement only when a documented trigger fires. See module docstring.
    """
    raise NotImplementedError(
        "hand_emitter.emit_handrolled() — RESERVE module. Do not implement until a documented "
        "trigger fires. See docs/edge-cases.md."
    )
