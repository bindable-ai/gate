import { generateKeyPairSync } from "node:crypto";
import { mkdtempSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import type { FetchLike } from "../src/index.js";

export const CREDENTIAL = "bnd_gt_TEST_SECRET_0123456789abcdef";
export const AGENT_UUID = "9b0c2f4e-1d2a-4c3b-8e5f-6a7b8c9d0e1f";
/** A made-up SCID, base58btc as did:webvh's is. */
export const AGENT_SCID = "QmWv9fYzR4h3TestScid2Gate8Bindab";
/** The agent's DID: the only identifier the Gate is given. */
export const AGENT_DID = `did:webvh:${AGENT_SCID}:bindable.example:agent:${AGENT_UUID}`;
/** The parallel did:web name, which the Gate refuses and never signs. */
export const AGENT_DID_WEB = `did:web:bindable.example:agent:${AGENT_UUID}`;
/** An agent key id, `<did>#<key uuid>`, made up. */
export const KEY_ID = `${AGENT_DID}#3c5e9a2b-7d14-4f0e-8b6a-2e9d1c4f7a05`;
export const BASE_URL = "https://api.bindable.example";
export const RECEIPT_ID = "5f7e3c1a-9b2d-4e6f-8a1c-3d5e7f9a1b2c";
export const DECIDED_AT = "2026-10-26T12:00:00.000Z";

export interface Sent {
  url: string;
  method: string;
  headers: Record<string, string>;
  body: Record<string, unknown>;
}

export type Answer = { status: number; body?: unknown } | Error | "hang";

/** A fetch that records each request and answers from `answer`. */
export function fakeFetch(answer: (sent: Sent) => Answer): { fetch: FetchLike; sent: Sent[] } {
  const sent: Sent[] = [];
  const fetch: FetchLike = async (url, init) => {
    const record: Sent = {
      url,
      method: String(init.method),
      headers: { ...(init.headers as Record<string, string>) },
      body: JSON.parse(String(init.body)) as Record<string, unknown>,
    };
    sent.push(record);
    const a = answer(record);
    if (a === "hang") {
      return new Promise<Response>((_resolve, reject) => {
        init.signal?.addEventListener("abort", () => reject(new DOMException("aborted", "AbortError")));
      });
    }
    if (a instanceof Error) throw a;
    return new Response(a.body === undefined ? "" : JSON.stringify(a.body), {
      status: a.status,
      headers: { "Content-Type": "application/json" },
    });
  };
  return { fetch, sent };
}

export function receipt(verdict: string, extra: Record<string, unknown> = {}): Record<string, unknown> {
  return { receipt_id: RECEIPT_ID, verdict, decided_at: DECIDED_AT, ...extra };
}

/** The platform's answer to a review, and 201 to every report. */
export function platform(verdict: string, extra: Record<string, unknown> = {}) {
  return fakeFetch((s) =>
    s.url.endsWith("/report")
      ? { status: 201, body: { trackpoint_id: "tp" } }
      : { status: 200, body: { verdict, failed_rule: extra.failed_rule ?? null, receipt: receipt(verdict, extra) } },
  );
}

export function tempDir(): string {
  return mkdtempSync(join(tmpdir(), "bindable-gate-test-"));
}

/** A P-256 key written as PEM to a temp file. */
export function p256KeyFile(dir = tempDir()) {
  const { privateKey, publicKey } = generateKeyPairSync("ec", { namedCurve: "prime256v1" });
  const path = join(dir, "agent-key.pem");
  writeFileSync(path, privateKey.export({ type: "pkcs8", format: "pem" }));
  return { path, privateKey, publicKey };
}

export function memoryLogger() {
  const lines: string[] = [];
  const at = (level: string) => (m: string) => lines.push(`${level} ${m}`);
  return { lines, logger: { debug: at("debug"), info: at("info"), warn: at("warn"), error: at("error") } };
}

export function connectedEnv(extra: Record<string, string> = {}): Record<string, string> {
  return {
    BINDABLE_AGENT_ID: AGENT_DID,
    BINDABLE_GATE_CREDENTIAL: CREDENTIAL,
    BINDABLE_API_BASE_URL: BASE_URL,
    ...extra,
  };
}
