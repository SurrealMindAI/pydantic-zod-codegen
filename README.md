# pydantic-zod-codegen

**Pydantic v2.13 → Zod v4. One source of truth, two type systems.**

Define your wire protocol once as Pydantic models. Get matching Zod schemas in
your TypeScript frontend — runtime-validated, statically-typed, drift-gated by
CI. No hand-maintained second copy.

```python
# Python (Pydantic — your source of truth)
class TreeNode(BaseModel):
    name: str
    children: list["TreeNode"] = []
```

```ts
// TypeScript (Zod — generated, never hand-edited)
export const TreeNode: z.ZodType<TreeNode> = z.lazy(() => z.object({
  name: z.string(),
  children: z.array(TreeNode).default([]),
}));
```

---

## Install

```bash
uv add pydantic-zod-codegen     # in a uv-managed project
# or
pip install pydantic-zod-codegen
```

### Runtime prerequisite

[bun](https://bun.com) must be on `PATH` so the pipeline can shell out to
`bunx json-schema-to-typescript@15`. Install via `brew install bun` or
`curl -fsSL https://bun.sh/install | bash`. No global Node install needed.

Verify after install:

```bash
pydantic-zod-codegen doctor   # exits 0 when bun + bunx are reachable
```

## Use

```bash
# 1. Generate Zod schemas from a Pydantic protocol module
pydantic-zod-codegen generate myapp.protocol -o frontend/src/lib/protocol.gen.ts

# 2. CI gate: regenerate and diff against the committed file
pydantic-zod-codegen check-drift myapp.protocol frontend/src/lib/protocol.gen.ts

# 3. Verify runtime prereqs (bun, json2ts reachable)
pydantic-zod-codegen doctor
```

`check-drift` exits non-zero with a unified diff when the committed
`.gen.ts` no longer matches what the pipeline would emit today — wire it into
your CI so a Pydantic change that someone forgot to regenerate becomes a
failed build, not a silent runtime mismatch.

## What it can do

Edge cases that break naïve codegen are covered by name. Each row below is a
load-bearing fixture in `tests/goldenfile/`.

### F-1 — Self-recursive models (`TreeNode`)

```python
# Pydantic
class TreeNode(BaseModel):
    name: str
    children: list["TreeNode"] = []
```

```ts
// Zod (with z.lazy to avoid TDZ ReferenceError on self-ref)
export interface TreeNode {
  name: string;
  children?: TreeNode[];
}

export const TreeNode: z.ZodType<TreeNode> = z.lazy(() => z.object({
  name: z.string(),
  children: z.array(TreeNode).default([]),
}));
```

### F-2 — Discriminated unions (`EventEnvelope`)

```python
# Pydantic
class ClickEvent(BaseModel):
    kind: Literal["click"]
    selector: str

class KeyboardEvent(BaseModel):
    kind: Literal["keyboard"]
    key: str

class EventEnvelope(BaseModel):
    event: Union[ClickEvent, KeyboardEvent] = Field(discriminator="kind")
```

```ts
// Zod — variant consts emitted BEFORE the union to avoid TDZ
export const ClickEvent = z.object({
  kind: z.literal("click"),
  selector: z.string(),
});

export const KeyboardEvent = z.object({
  kind: z.literal("keyboard"),
  key: z.string(),
});

export const EventEnvelope = z.object({
  event: z.discriminatedUnion("kind", [ClickEvent, KeyboardEvent]),
});
```

### F-4 — Optional/nullable tristate (`User`)

Three distinct "missing" semantics that protocols routinely conflate:

```python
# Pydantic
class User(BaseModel):
    name: str                          # required, non-nullable
    nickname: str | None = None        # optional with default (NOT in `required`)
    age: int | None                    # required-but-nullable
```

```ts
// Zod — modifier chain encodes the exact wire semantics
export const User = z.object({
  name: z.string(),
  nickname: z.string().nullable().optional().default(null),
  age: z.number().int().nullable(),
});
```

### F-5 — Annotated metadata (`Username`)

`json-schema-to-typescript` silently drops `Field(...)` constraints. We
restore them as runtime checks on the Zod side:

```python
# Pydantic
class Username(BaseModel):
    value: Annotated[str, Field(min_length=3, max_length=32, pattern=r"^[a-z]+$")]
```

```ts
// Zod — constraints surface as runtime validation
export const Username = z.object({
  value: z.string().min(3).max(32).regex(/^[a-z]+$/),
});
```

### F-7 — Standard formats

Pydantic `EmailStr`, `UUID4`, `datetime`, `HttpUrl`, etc. map to Zod v4 top-level
format helpers (`z.email()`, `z.uuid()`, `z.iso.datetime()`, `z.url()`).

See [`docs/edge-cases.md`](docs/edge-cases.md) for the full F-class matrix
(F-1 .. F-7) with component-ownership annotations.

## Codegen contract

Models discovered via `__codegen_roots__` (preferred) or `__all__`:

```python
# myapp/protocol.py
class TreeNode(BaseModel): ...
class EventEnvelope(BaseModel): ...
class ClickEvent(BaseModel): ...
class KeyboardEvent(BaseModel): ...
class User(BaseModel): ...
class Username(BaseModel): ...

# Curated, ordered list of TOP-LEVEL types the pipeline emits. Variant types
# (ClickEvent / KeyboardEvent) and types reserved for the dormant hand-emitter
# can stay in `__all__` but omit them here so they aren't emitted at root level.
__codegen_roots__ = [
    "TreeNode",
    "EventEnvelope",
    "User",
    "Username",
]
```

## How it works

```
Pydantic v2.13 model
  → CustomGenerateJsonSchema       (Pydantic-side hook: titles, additionalProperties, discriminator mapping)
  → normalise_for_json2ts          ($defs → definitions string rewrite)
  → bunx json-schema-to-typescript@15  (subprocess via bun)
  → post_process_zod               (Zod v4 augmentation + self-ref/forward-ref TDZ guards)
  → drift.regenerate               (concat per-model chunks, dedupe declarations)
  → committed .gen.ts
```

`check-drift` reruns the pipeline and diffs against the committed file.

Full design rationale in [`ARCHITECTURE.md`](ARCHITECTURE.md). Contributor
guide in [`DEVELOPMENT.md`](DEVELOPMENT.md). Failure-mode taxonomy in
[`docs/edge-cases.md`](docs/edge-cases.md).

## Develop

```bash
uv sync                          # install runtime + dev deps
uv run pytest -v                 # full suite, ~2s
uv run pytest -m drift           # just the drift gate
uv run ruff check src tests
uv run ruff format src tests
uv run mypy src
```

When you edit a fixture or change pipeline logic, regenerate the goldenfile:

```bash
uv run pydantic-zod-codegen generate tests.goldenfile.fixtures.models \
    -o tests/goldenfile/expected/protocol.gen.ts
```

CI runs lint + tests on every push and PR; pushing a `vX.Y.Z` tag publishes to
PyPI via GitHub Actions. See [`.github/workflows/`](.github/workflows).

## Where it fits

Designed for a Pydantic-as-single-source-of-truth wire protocol: define your
models once in Python, generate the matching Zod schemas, and validate at
runtime on both ends. It pairs naturally with any Python WebSocket/JSON-RPC
server and any TypeScript frontend that consumes Zod schemas.

The contract flows: Pydantic model → Zod schema (via this lib) → runtime
validation on the TypeScript side.

## Design

The pipeline and its edge-case handling (F-1 through F-7) are documented in
[`ARCHITECTURE.md`](ARCHITECTURE.md) and [`docs/edge-cases.md`](docs/edge-cases.md).
The approach extends the FastUI hybrid pattern (CustomGenerateJsonSchema →
`json-schema-to-typescript`) with a Zod-v4 emission stage and a CI drift gate.

## License

MIT.
