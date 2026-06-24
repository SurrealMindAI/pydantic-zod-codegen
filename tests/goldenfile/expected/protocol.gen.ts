/* eslint-disable */
/* AUTO-GENERATED — pydantic-zod-codegen pipeline output. DO NOT HAND-EDIT. */
import { z } from "zod";

/**
 * Self-referential — historically the crash case for json2ts baseline.
 */
export interface TreeNode {
  name: string;
  children?: TreeNode[];
}

export const TreeNode: z.ZodType<TreeNode> = z.lazy(() => z.object({
  name: z.string(),
  children: z.array(TreeNode).default([]),
}));

/**
 * Field-discriminator form: `Field(discriminator=...)`.
 */
export interface EventEnvelope {
  event: ClickEvent | KeyboardEvent;
}
export interface ClickEvent {
  kind: "click";
  selector: string;
}
export interface KeyboardEvent {
  kind: "keyboard";
  key: string;
}

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

/**
 * Three distinct "missing" semantics — protocols routinely conflate these.
 *
 * - `name: str`               required, non-nullable
 * - `nickname: str | None = None`  optional with default, NOT in `required`
 * - `age: int | None`         required-but-nullable (in `required`, type is union)
 */
export interface User {
  name: string;
  nickname?: string | null;
  age: number | null;
}

export const User = z.object({
  name: z.string(),
  nickname: z.string().nullable().optional().default(null),
  age: z.number().int().nullable(),
});

/**
 * `Annotated` metadata that json2ts SILENTLY DROPS — runtime drift risk.
 *
 * A correct Zod emission must preserve `min_length`, `max_length`, `pattern`
 * as runtime checks AND as JSDoc on the inferred type.
 */
export interface Username {
  value: string;
}

export const Username = z.object({
  value: z.string().min(3).max(32).regex(/^[a-z]+$/),
});

export type EventAnnotated = ClickEvent | KeyboardEvent;

export const EventAnnotated = z.discriminatedUnion("kind", [ClickEvent, KeyboardEvent]);

export type RelationKind = "relates_to" | "triggers" | "part_of";

export const RelationKind = z.union([z.literal("relates_to"), z.literal("triggers"), z.literal("part_of")]);
