# Development

Contributor guide for `pydantic-zod-codegen`. For the design rationale see
[`ARCHITECTURE.md`](ARCHITECTURE.md); for the failure-mode taxonomy see
[`docs/edge-cases.md`](docs/edge-cases.md).

## One-line purpose

Pydantic v2.13 → JSON-Schema → Zod v4 codegen pipeline (FastUI hybrid).

## Stack

- Python **3.13+**, pydantic **>=2.13,<3**
- [uv](https://docs.astral.sh/uv/) for environment management
- [bun](https://bun.com) for `bunx json-schema-to-typescript@15` (no global node install)
- ruff + mypy (strict) + pytest + hypothesis

## Commands

| Task | Command |
|------|---------|
| Bootstrap | `uv sync` |
| Run tests | `uv run pytest -v` |
| Run a specific marker | `uv run pytest -m property` / `uv run pytest -m drift` |
| Lint | `uv run ruff check src tests` |
| Format | `uv run ruff format src tests` |
| Type-check | `uv run mypy src` |
| CLI | `uv run pydantic-zod-codegen generate <module> -o <file>` |

## Layer boundaries (pipeline)

```
Pydantic models
  -> CustomGenerateJsonSchema (custom_schema.py)        # Pydantic-side hook
  -> normalise_for_json2ts (pre_processor.py)           # 5-line $defs rewrite
  -> emit_typescript (emitter.py)                       # bunx json2ts
  -> post_process_zod (post_processor.py)               # Zod v4 augmentation
  -> committed .gen.ts
```

Drift check (`drift.py`) reruns the pipeline and diffs against the committed
file. The hand-emitter (`hand_emitter.py`) is RESERVE — dormant until a
documented trigger fires (see [`ARCHITECTURE.md`](ARCHITECTURE.md) §8).

## Stale-state hygiene

**When to re-generate** the committed `.gen.ts`:

- a Pydantic model in the protocol module changed
- a library version bump (pydantic, json2ts, zod)
- `CustomGenerateJsonSchema` overrides changed
- pre/post-processor logic changed

**How you'll know you forgot**: CI `pytest -m drift` fails with a unified diff.
Locally: `uv run pydantic-zod-codegen check-drift <module> <committed-path>`.

**Do NOT** hand-edit `.gen.ts` files — they are SSoT-derived artifacts and
hand-edits are silently lost on the next regeneration.

## Reading order for new contributors

1. [`README.md`](README.md) — what + why + how to run
2. [`ARCHITECTURE.md`](ARCHITECTURE.md) — pipeline design, decisions, diagram
3. [`docs/edge-cases.md`](docs/edge-cases.md) — F-1..F-7 with component ownership
4. `src/pydantic_zod_codegen/__init__.py` — the public API surface
5. `src/pydantic_zod_codegen/pre_processor.py` — start here, smallest scope, the empirical must-have
6. `tests/goldenfile/fixtures/models.py` — the canonical edge-case models

## Contributing

- Keep the test pyramid lean: hand-picked property tests + the drift gate, not
  an exhaustive type matrix (see [`ARCHITECTURE.md`](ARCHITECTURE.md) §11).
- Run `ruff`, `mypy`, and `pytest` green before opening a PR.
- If you regenerate fixtures, commit the regenerated `.gen.ts` in the same PR so
  the drift gate stays green.
