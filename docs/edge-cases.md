# Edge Cases (F-1 .. F-7)

This is the **WHY** of every module in `src/pydantic_zod_codegen/`. Each
F-class is a real Pydantic shape that breaks naive codegen. The pipeline
allocates one component per failure mode.

## Failure mode -> component ownership

| Class | Pydantic shape | Naive failure | Owning component |
|-------|----------------|---------------|------------------|
| **F-1** | recursive — `TreeNode { children: list["TreeNode"] }` | `json2ts` crash on `%24defs` URL-encoded refs | `pre_processor.py` (the 5-line core) |
| **F-2** | discriminated union — `Union[A, B] = Field(discriminator="kind")` | Emits plain `A \| B` — loses runtime narrowing | `custom_schema.py` (Pydantic-side) + `post_processor.py` (Zod-side) |
| **F-3** | generic — `Page[T]`, `Page[Item]` | JSON Schema flattens `Page[Item]` -> `Page_Item_`; generic lost | `hand_emitter.py` (escape hatch — `__pydantic_generic_metadata__`) |
| **F-4** | tristate optional/nullable — `x: str \| None = None` vs `x: str \| None` | Pydantic puts both in `type: ["string", "null"]`; Zod must distinguish `.optional()` vs `.nullable()` | `post_processor.py` (uses `is_required()` lookup from source schema) |
| **F-5** | annotated metadata — `Annotated[str, Field(min_length=3, pattern=...)]` | `json2ts` silently drops constraints — runtime drift risk | `post_processor.py` (brand augmentation + JSDoc) |
| **F-6** | template-literal pattern — `Annotated[str, Field(pattern=r"^[A-Z]+$")]` | Emits plain `string` — loses static type safety | `post_processor.py` (template-literal type emission) |
| **F-7** | datetime / UUID / Decimal | Emits `string` — no Zod format check, no brand | `post_processor.py` (format check + brand wrap) |

## Why this layout

The pipeline is one stage per **fix**, not one stage per **type**:

- F-1 is fixed at the JSON-Schema layer (cheaper than re-emitting TS).
- F-2 is split: schema-side hook for naming convention, post-processor for Zod
  emission shape (`z.discriminatedUnion`).
- F-3 is the ONE class we cannot fix in the json2ts pipeline at all — the
  generic information is gone before json2ts sees it. That is why we keep the
  hand-emitter as a documented escape hatch.
- F-4..F-7 are all schema-aware post-processing. They need the source schema
  alongside the TS output to do their work.

## Reference

- FastUI's `generate_typescript.py` — production reference for the
  hybrid pattern (Pydantic team's own codegen).

## Hand-emitter activation triggers

By design, the hand-emitter is **dormant by default**. Activate
when ONE of these holds (none active today):

1. 5+ `Generic[T]` envelope types in the protocol — `Page_Item_` / `Result_X_`
   naming proliferation kills review readability.
2. `Annotated` metadata is semantically load-bearing on the wire AND
   post-processor cannot cleanly preserve it.
3. Pydantic v3 lands and breaks `model_json_schema()` so badly that
   re-implementing `pre_processor.py` costs more than activating the emitter.
