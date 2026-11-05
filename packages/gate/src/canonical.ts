/**
 * JSON values, their RFC 8785 canonical form, and SHA-256 over it.
 *
 * Canonicalisation is the maintained `canonicalize` library's, not a copy.
 */

import { createHash } from "node:crypto";
import canonicalize from "canonicalize";

/**
 * `value` as plain JSON: what `JSON.stringify` keeps, with a BigInt written as
 * its decimal string. `undefined` at the top becomes `null`.
 */
export function toJson(value: unknown): unknown {
  const text = JSON.stringify(value, (_key, v) => (typeof v === "bigint" ? v.toString() : v));
  return text === undefined ? null : JSON.parse(text);
}

/** The RFC 8785 canonical JSON of `value`, after {@link toJson}. */
export function canonicalJson(value: unknown): string {
  const text = canonicalize(toJson(value));
  if (typeof text !== "string") throw new TypeError("the value has no canonical JSON form");
  return text;
}

/** Lower-case hex SHA-256 of a string's UTF-8 bytes. */
export function sha256Hex(text: string): string {
  return createHash("sha256").update(text, "utf8").digest("hex");
}

/** Lower-case hex SHA-256 of the canonical JSON of `value`. */
export function canonicalSha256(value: unknown): string {
  return sha256Hex(canonicalJson(value));
}
