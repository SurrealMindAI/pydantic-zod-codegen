"""The five canonical Pydantic models — one per F-class edge case.

These models are NOT examples — they are the test pyramid's load-bearing
fixtures. The hybrid pipeline must produce stable, reviewable output for each.

Source: the canonical edge-case fixtures (see docs/edge-cases.md).

DO NOT add models that test the same F-class twice. The whole point is that
3-5 well-chosen fixtures > N trivial ones. If you need a new fixture, it must
map to a NEW F-class or document why the existing fixtures are insufficient.

F-class mapping:
    F-1 recursive             -> TreeNode
    F-2 discriminated union   -> EventEnvelope, EventAnnotated
    F-3 generic               -> Page[Item], PageOfItems
    F-4 optional vs nullable  -> User
    F-5 annotated metadata    -> Username
    F-8 literal alias         -> RelationKind
"""

from __future__ import annotations

from typing import Annotated, Generic, Literal, TypeVar, Union

from pydantic import BaseModel, Discriminator, Field

# ---------- F-1: Recursive ----------


class TreeNode(BaseModel):
    """Self-referential — historically the crash case for json2ts baseline."""

    name: str
    children: list["TreeNode"] = []


# ---------- F-2: Discriminated union (both forms) ----------


class ClickEvent(BaseModel):
    kind: Literal["click"]
    selector: str


class KeyboardEvent(BaseModel):
    kind: Literal["keyboard"]
    key: str


class EventEnvelope(BaseModel):
    """Field-discriminator form: `Field(discriminator=...)`."""

    event: Union[ClickEvent, KeyboardEvent] = Field(discriminator="kind")


EventAnnotated = Annotated[Union[ClickEvent, KeyboardEvent], Discriminator("kind")]


# ---------- F-3: Generic[T] (the only F-class json2ts CANNOT preserve) ----------

T = TypeVar("T")


class Item(BaseModel):
    id: int
    label: str


class Page(BaseModel, Generic[T]):
    items: list[T]
    total: int


class PageOfItems(BaseModel):
    """Concrete instantiation — JSON Schema flattens away the generic."""

    page: Page[Item]


# ---------- F-4: Optional vs nullable tristate ----------


class User(BaseModel):
    """Three distinct "missing" semantics — protocols routinely conflate these.

    - `name: str`               required, non-nullable
    - `nickname: str | None = None`  optional with default, NOT in `required`
    - `age: int | None`         required-but-nullable (in `required`, type is union)
    """

    name: str
    nickname: str | None = None
    age: int | None


# ---------- F-5: Annotated metadata ----------


class Username(BaseModel):
    """`Annotated` metadata that json2ts SILENTLY DROPS — runtime drift risk.

    A correct Zod emission must preserve `min_length`, `max_length`, `pattern`
    as runtime checks AND as JSDoc on the inferred type.
    """

    value: Annotated[str, Field(min_length=3, max_length=32, pattern=r"^[a-z]+$")]


# ---------- F-8: Top-level Literal type-alias ----------

RelationKind = Literal["relates_to", "triggers", "part_of"]
"""Top-level Literal alias — must emit as `export type RelationKind = "..." | ...;`.

Motivating case: a real-world protocol with a string-union SSoT that should be
consumable verbatim from the TypeScript side without going through a
`Model['field']`-indexing workaround.
"""


__all__ = [
    "ClickEvent",
    "EventAnnotated",
    "EventEnvelope",
    "Item",
    "KeyboardEvent",
    "Page",
    "PageOfItems",
    "RelationKind",
    "TreeNode",
    "User",
    "Username",
]


# Codegen contract — the ordered list of TOP-LEVEL models the pipeline emits.
#
# `__all__` lists every public symbol (including nested variants like
# `ClickEvent`/`KeyboardEvent`, which are inlined into their discriminated-union
# envelope, and F-3 generic types `Item`/`Page`/`PageOfItems`, which are RESERVED
# for the dormant `hand_emitter.py` per ARCHITECTURE.md §8). `drift.regenerate`
# walks this curated list instead — it expresses the protocol's wire shape, in
# the exact order the committed `.gen.ts` materialises.
#
# Convention: any module consumed by `pydantic-zod-codegen` MAY declare
# `__codegen_roots__` to override discovery; without it the library falls back
# to `__all__` order.
__codegen_roots__ = [
    "TreeNode",
    "EventEnvelope",
    "User",
    "Username",
    "EventAnnotated",
    "RelationKind",
]
