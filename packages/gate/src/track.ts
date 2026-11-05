/**
 * The local Track: what an agent did while no Gate credential was configured.
 *
 * One JSON line per tool call in a hash-chained file. Each line's
 * `chain_hash` is SHA-256 over the RFC 8785 canonical JSON of the line without
 * `chain_hash` and `agent_signature`, and that includes `previous_hash`, the
 * chain hash of the line before (64 zeros for the first). Changing, removing
 * or reordering any line breaks every hash after it. With an agent key, each
 * line also carries `agent_signature` over the same bytes.
 *
 * The local Track is a record kept on this machine. Bindable has reviewed
 * none of it.
 */

import { randomUUID, type KeyObject } from "node:crypto";
import { appendFileSync, closeSync, existsSync, fstatSync, mkdirSync, openSync, readFileSync, readSync } from "node:fs";
import { homedir } from "node:os";
import { dirname, join } from "node:path";
import { canonicalJson, canonicalSha256, sha256Hex } from "./canonical.js";
import { GateConfigError } from "./errors.js";
import { verifySignature, type Signer } from "./sign.js";

export const GENESIS_HASH = "0".repeat(64);
const HASH = /^[0-9a-f]{64}$/;
const PREVIEW_CHARS = 200;
const ERROR_CHARS = 500;

/** Where the local Track is kept unless told otherwise. */
export function defaultLocalTrackPath(): string {
  return join(homedir(), ".bindable", "local-track.jsonl");
}

/** What a line says about a call's result: its digest and the start of it. */
export interface ResultSummary {
  digest: string | null;
  preview: string;
}

/** One line of the local Track. */
export interface LocalTrackEntry {
  id: string;
  previous_hash: string;
  chain_hash: string;
  agent_id: string | null;
  tool: string;
  params: Record<string, unknown>;
  result_summary: ResultSummary | null;
  error: string | null;
  duration_ms: number | null;
  started_at: string;
  finished_at: string;
  agent_key_id?: string;
  signed_at?: string;
  agent_signature?: string;
}

/** One call, as the host saw it. */
export interface LocalTrackCall {
  tool: string;
  params: Record<string, unknown>;
  startedAt: string;
  result?: unknown;
  error?: string;
  durationMs?: number;
}

export interface LocalTrackOptions {
  path?: string;
  /** The agent's did:webvh DID, where one is configured. */
  agentId?: string;
  signer?: Signer;
  now?: () => Date;
}

/** The bytes a line's chain hash and signature cover. */
function coveredBytes(entry: Omit<LocalTrackEntry, "chain_hash" | "agent_signature">): string {
  return canonicalJson(entry);
}

/** A result in a line: its digest and the first characters of its JSON. */
export function summarise(result: unknown): ResultSummary | null {
  if (result === undefined) return null;
  let json: string;
  try {
    json = canonicalJson(result);
  } catch {
    return { digest: null, preview: "" };
  }
  return { digest: sha256Hex(json), preview: json.slice(0, PREVIEW_CHARS) };
}

export class LocalTrack {
  readonly path: string;
  readonly #agentId: string | null;
  readonly #signer: Signer | undefined;
  readonly #now: () => Date;
  #previous: string | undefined;

  constructor(options: LocalTrackOptions = {}) {
    this.path = options.path ?? defaultLocalTrackPath();
    this.#agentId = options.agentId ?? null;
    this.#signer = options.signer;
    this.#now = options.now ?? (() => new Date());
  }

  /**
   * Make the folder and find where the chain ends. Synchronous, for a host
   * that registers synchronously.
   *
   * @throws GateConfigError when the file's last line is not a local Track line.
   */
  open(): void {
    mkdirSync(dirname(this.path), { recursive: true, mode: 0o700 });
    this.#previous = existsSync(this.path) ? lastChainHash(this.path) : GENESIS_HASH;
  }

  /**
   * Append one call and return its line. Synchronous, so lines never
   * interleave within this process. The chain's end is read again first,
   * because another writer (the Python Gate's hook, say) may have appended
   * since; Node has no file lock, so two writers appending in the same
   * instant can still fork the chain, and the check names the line.
   */
  append(call: LocalTrackCall): LocalTrackEntry {
    if (this.#previous === undefined) this.open();
    this.#previous = existsSync(this.path) ? lastChainHash(this.path) : GENESIS_HASH;
    const finishedAt = this.#now().toISOString();
    const body: Omit<LocalTrackEntry, "chain_hash" | "agent_signature"> = {
      id: randomUUID(),
      previous_hash: this.#previous as string,
      agent_id: this.#agentId,
      tool: call.tool,
      params: call.params,
      result_summary: call.error === undefined ? summarise(call.result) : null,
      error: call.error === undefined ? null : String(call.error).slice(0, ERROR_CHARS),
      duration_ms: typeof call.durationMs === "number" && Number.isFinite(call.durationMs) ? call.durationMs : null,
      started_at: call.startedAt,
      finished_at: finishedAt,
      ...(this.#signer ? { agent_key_id: this.#signer.keyId, signed_at: finishedAt } : {}),
    };
    const bytes = coveredBytes(body);
    const entry: LocalTrackEntry = {
      ...(JSON.parse(bytes) as typeof body),
      chain_hash: sha256Hex(bytes),
      ...(this.#signer ? { agent_signature: this.#signer.sign(bytes) } : {}),
    };
    appendFileSync(this.path, JSON.stringify(entry) + "\n", { encoding: "utf8", mode: 0o600 });
    this.#previous = entry.chain_hash;
    return entry;
  }
}

/** The chain hash on the file's last line, read from the end of the file. */
function lastChainHash(path: string): string {
  const fd = openSync(path, "r");
  try {
    const size = fstatSync(fd).size;
    if (size === 0) return GENESIS_HASH;
    const chunk = 64 * 1024;
    let end = size;
    let tail = "";
    while (end > 0) {
      const start = Math.max(0, end - chunk);
      const buffer = Buffer.alloc(end - start);
      readSync(fd, buffer, 0, buffer.length, start);
      tail = buffer.toString("utf8") + tail;
      end = start;
      const trimmed = tail.replace(/\n+$/, "");
      if (trimmed.includes("\n") || end === 0) {
        const line = trimmed.slice(trimmed.lastIndexOf("\n") + 1);
        if (line === "") return GENESIS_HASH;
        let parsed: unknown;
        try {
          parsed = JSON.parse(line);
        } catch {
          parsed = undefined;
        }
        const hash = (parsed as { chain_hash?: unknown } | undefined)?.chain_hash;
        if (typeof hash !== "string" || !HASH.test(hash)) {
          throw new GateConfigError("the local Track's last line is not a local Track line");
        }
        return hash;
      }
    }
    return GENESIS_HASH;
  } finally {
    closeSync(fd);
  }
}

/** What checking a local Track found. */
export interface LocalTrackCheck {
  ok: boolean;
  lines: number;
  /** The 1-based line where the chain first breaks. */
  brokenAt?: number;
  reason?: string;
}

/**
 * Recompute the whole chain. With `publicKey`, every line must also carry a
 * signature that key made.
 */
export function verifyLocalTrack(path: string, publicKey?: KeyObject): LocalTrackCheck {
  const lines = readFileSync(path, "utf8").split("\n").filter((l) => l !== "");
  let previous = GENESIS_HASH;
  for (let i = 0; i < lines.length; i += 1) {
    const at = i + 1;
    let entry: LocalTrackEntry;
    try {
      entry = JSON.parse(lines[i] as string) as LocalTrackEntry;
    } catch {
      return { ok: false, lines: lines.length, brokenAt: at, reason: "not JSON" };
    }
    const { chain_hash, agent_signature, ...body } = entry;
    if (body.previous_hash !== previous) {
      return { ok: false, lines: lines.length, brokenAt: at, reason: "previous_hash does not match" };
    }
    const bytes = coveredBytes(body);
    if (canonicalSha256(body) !== chain_hash) {
      return { ok: false, lines: lines.length, brokenAt: at, reason: "chain_hash does not match" };
    }
    if (publicKey && (typeof agent_signature !== "string" || !verifySignature(publicKey, bytes, agent_signature))) {
      return { ok: false, lines: lines.length, brokenAt: at, reason: "agent_signature does not verify" };
    }
    previous = chain_hash;
  }
  return { ok: true, lines: lines.length };
}
