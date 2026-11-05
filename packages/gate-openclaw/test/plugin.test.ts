import { generateKeyPairSync } from "node:crypto";
import { mkdirSync, mkdtempSync, readFileSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { beforeEach, describe, expect, it } from "vitest";
import { agentFolder, canonicalSha256, verifyLocalTrack } from "@bindable-ai/gate";
import { GATE_VERSION, HOOK_TIMEOUT_MS, createPlugin, resetLocalNotice } from "../src/plugin.js";
import type { PluginApi } from "../src/openclaw.js";

const CREDENTIAL = "bnd_gt_PLUGIN_SECRET_fedcba9876543210";
const AGENT_UUID = "9b0c2f4e-1d2a-4c3b-8e5f-6a7b8c9d0e1f";
const AGENT_DID = `did:webvh:QmWv9fYzR4h3TestScid2Gate8Bindab:bindable.example:agent:${AGENT_UUID}`;

/** The setting the platform last gave this agent, as offline mode keeps it. */
function keepOfflineSetting(agentId: string, setting: string): void {
  const folder = agentFolder(agentId);
  mkdirSync(folder, { recursive: true });
  writeFileSync(join(folder, "setting.json"), JSON.stringify({ when_unreachable: setting, read_at: new Date().toISOString() }));
}
const BASE = "https://api.bindable.example";
const RECEIPT_ID = "5f7e3c1a-9b2d-4e6f-8a1c-3d5e7f9a1b2c";

const env = (extra: Record<string, string> = {}) => ({
  BINDABLE_AGENT_ID: AGENT_DID,
  BINDABLE_GATE_CREDENTIAL: CREDENTIAL,
  BINDABLE_API_BASE_URL: BASE,
  ...extra,
});

interface Sent {
  url: string;
  headers: Record<string, string>;
  body: Record<string, unknown>;
}

function bindable(answer: (s: Sent) => { status: number; body?: unknown } | Error) {
  const sent: Sent[] = [];
  const fetch = async (url: string, init: RequestInit) => {
    const s = { url, headers: init.headers as Record<string, string>, body: JSON.parse(String(init.body)) };
    sent.push(s);
    const a = answer(s);
    if (a instanceof Error) throw a;
    return new Response(a.body === undefined ? "" : JSON.stringify(a.body), { status: a.status });
  };
  return { fetch, sent };
}

const verdict = (v: string, extra: Record<string, unknown> = {}) =>
  bindable((s) =>
    s.url.endsWith("/report")
      ? { status: 201, body: {} }
      : { status: 200, body: { verdict: v, receipt: { receipt_id: RECEIPT_ID, verdict: v, decided_at: "2026-10-26T12:00:00.000Z", ...extra } } },
  );

type Handler = (...args: unknown[]) => unknown;

function host() {
  const handlers: Record<string, Handler> = {};
  const options: Record<string, unknown> = {};
  const logs: string[] = [];
  const at = (level: string) => (m: string) => logs.push(`${level} ${m}`);
  const api = {
    logger: { debug: at("debug"), info: at("info"), warn: at("warn"), error: at("error") },
    on(name: string, handler: Handler, opts?: unknown) {
      handlers[name] = handler;
      options[name] = opts;
    },
  } as unknown as PluginApi;
  return { api, handlers, options, logs };
}

const event = {
  toolName: "exec",
  params: { command: "ls -la" },
  toolKind: "code_mode_exec",
  runId: "run-1",
  toolCallId: "call-1",
  derivedPaths: ["/work/a.txt"],
};
const ctx = { agentId: "main", sessionKey: "agent:main:main", toolName: "exec" };

function load(options: Parameters<typeof createPlugin>[0]) {
  const h = host();
  createPlugin(options).register(h.api);
  return h;
}

beforeEach(() => resetLocalNotice());

describe("registration", () => {
  it("hooks every tool: no matcher, the timeout pinned above the Gate's deadline", () => {
    const { handlers, options } = load({ env: env(), fetch: verdict("passed").fetch });
    expect(Object.keys(handlers).sort()).toEqual(["after_tool_call", "before_tool_call", "gateway_stop"]);
    expect(options.before_tool_call).toEqual({ timeoutMs: HOOK_TIMEOUT_MS });
    expect(options.after_tool_call).toBeUndefined();
  });

  it("entry shape OpenClaw loads", () => {
    const entry = createPlugin({ env: {} });
    expect(entry.id).toBe("bindable-gate");
    const manifest = JSON.parse(readFileSync(new URL("../openclaw.plugin.json", import.meta.url), "utf8"));
    expect(manifest.id).toBe(entry.id);
  });
});

describe("before_tool_call", () => {
  it("Released: returns nothing; the Proposal carries what OpenClaw says about the call", async () => {
    const { fetch, sent } = verdict("passed");
    const { handlers } = load({ env: env(), fetch });
    expect(await handlers.before_tool_call!(event, ctx)).toBeUndefined();
    expect(sent[0]!.body).toMatchObject({
      agent_id: AGENT_DID,
      action_type: "exec",
      review_context: "test",
      payload: {
        params: { command: "ls -la" },
        toolKind: "code_mode_exec",
        derivedPaths: ["/work/a.txt"],
        runId: "run-1",
        toolCallId: "call-1",
        agentId: "main",
        sessionKey: "agent:main:main",
      },
    });
  });

  it("Blocked: { block: true, blockReason } with Bindable's reason", async () => {
    const { fetch } = verdict("failed", { failed_rule: "no-delete", reason_detail: "Deleting files is not allowed." });
    const { handlers } = load({ env: env(), fetch });
    expect(await handlers.before_tool_call!(event, ctx)).toEqual({
      block: true,
      blockReason: `Action Gate blocked exec. Action Review returned failed under the rule no-delete. Deleting files is not allowed. Receipt ${RECEIPT_ID}.`,
    });
  });

  it("Bindable unreachable: blocked, decision unavailable, under stop the agent", async () => {
    keepOfflineSetting(AGENT_DID, "stop");
    const { fetch } = bindable(() => new TypeError("fetch failed"));
    const { handlers } = load({ env: env(), fetch });
    expect(await handlers.before_tool_call!(event, ctx)).toEqual({ block: true, blockReason: "decision unavailable" });
  });

  it("Bindable answers non-2xx: blocked, decision unavailable, under stop the agent", async () => {
    keepOfflineSetting(AGENT_DID, "stop");
    for (const status of [400, 401, 403, 404, 422, 429, 500, 502, 503]) {
      const { fetch } = bindable(() => ({ status, body: { code: "x" } }));
      const { handlers } = load({ env: env(), fetch });
      expect(await handlers.before_tool_call!(event, ctx)).toEqual({ block: true, blockReason: "decision unavailable" });
    }
  });
});

describe("offline mode", () => {
  const ASK =
    "Bindable is unreachable. This agent's actions are not being reviewed. To continue anyway run: bindable-gate offline accept";

  it("ask me first, the default: blocked with the person's words", async () => {
    const { fetch } = bindable(() => new TypeError("fetch failed"));
    const { handlers, logs } = load({ env: env(), fetch });
    expect(await handlers.before_tool_call!(event, ctx)).toEqual({ block: true, blockReason: ASK });
    expect(logs).toContain(`warn ${ASK}`);
  });

  it("switch automatically: the call runs, is said once, and becomes an offline record", async () => {
    keepOfflineSetting(AGENT_DID, "automatic");
    const { fetch, sent } = bindable(() => new TypeError("fetch failed"));
    const { handlers, logs } = load({ env: env(), fetch });
    expect(await handlers.before_tool_call!(event, ctx)).toBeUndefined();
    await handlers.after_tool_call!({ ...event, result: { content: [{ type: "text", text: "ok" }] } }, ctx);
    expect(logs).toContain("warn Bindable is unreachable. This agent's actions are not being reviewed.");
    const [record] = readFileSync(join(agentFolder(AGENT_DID), "records-gate-openclaw.jsonl"), "utf8").split("\n").filter(Boolean).map((l) => JSON.parse(l));
    expect(record).toMatchObject({ tool: "exec", allowed_by: "automatic", failed: false });
    expect(record).not.toHaveProperty("receipt_id");
    expect(sent.filter((s) => s.url.endsWith("/report"))).toHaveLength(0);
  });
});

describe("after_tool_call", () => {
  it("reports the enforcement outcome of a call that ran", async () => {
    const { fetch, sent } = verdict("passed");
    const { handlers } = load({ env: env(), fetch });
    await handlers.before_tool_call!(event, ctx);
    await handlers.after_tool_call!({ ...event, result: { content: [{ type: "text", text: "ok" }] }, durationMs: 41 }, ctx);
    expect(sent[1]!.url).toBe(`${BASE}/v1/receipts/${RECEIPT_ID}/report`);
    expect(sent[1]!.body).toMatchObject({
      outcome: "released",
      gate_kind: "runtime_plugin",
      gate_version: GATE_VERSION,
      action_result_digest: canonicalSha256({ content: [{ type: "text", text: "ok" }] }),
      proposed_at: sent[0]!.body.proposed_at,
    });
  });

  it("reports a call that failed while running as released, with no digest", async () => {
    const { fetch, sent } = verdict("passed");
    const { handlers } = load({ env: env(), fetch });
    await handlers.before_tool_call!(event, ctx);
    await handlers.after_tool_call!({ ...event, error: "command not found", durationMs: 2 }, ctx);
    expect(sent[1]!.body).toMatchObject({ outcome: "released", action_result_digest: null });
  });
});

describe("a wrong key file fails at startup, closed", () => {
  it("does not throw, says why once, and blocks every call", async () => {
    const dir = mkdtempSync(join(tmpdir(), "gate-openclaw-"));
    const path = join(dir, "key.pem");
    const { privateKey } = generateKeyPairSync("ec", { namedCurve: "secp384r1" });
    writeFileSync(path, privateKey.export({ type: "pkcs8", format: "pem" }));
    const { fetch, sent } = verdict("passed");
    const { handlers, logs } = load({ env: env({ BINDABLE_AGENT_KEY_FILE: path, BINDABLE_AGENT_KEY_ID: `${AGENT_DID}#3c5e9a2b-7d14-4f0e-8b6a-2e9d1c4f7a05` }), fetch });
    expect(logs).toEqual([
      "error Bindable Gate: configuration invalid, so every tool call is blocked: BINDABLE_AGENT_KEY_FILE is not an ECDSA P-256 private key",
    ]);
    expect(await handlers.before_tool_call!(event, ctx)).toEqual({
      block: true,
      blockReason: "Bindable Gate configuration invalid: BINDABLE_AGENT_KEY_FILE is not an ECDSA P-256 private key",
    });
    expect(sent).toHaveLength(0);
    expect(handlers.after_tool_call).toBeUndefined();
  });
});

describe("signing", () => {
  const KEY_ID = `${AGENT_DID}#3c5e9a2b-7d14-4f0e-8b6a-2e9d1c4f7a05`;

  it("with a key file: says so once at startup, and every Proposal is signed", async () => {
    const path = join(mkdtempSync(join(tmpdir(), "gate-openclaw-")), "key.pem");
    const { privateKey } = generateKeyPairSync("ec", { namedCurve: "prime256v1" });
    writeFileSync(path, privateKey.export({ type: "pkcs8", format: "pem" }));
    const { fetch, sent } = verdict("passed");
    const { handlers, logs } = load({ env: env({ BINDABLE_AGENT_KEY_FILE: path, BINDABLE_AGENT_KEY_ID: KEY_ID }), fetch });
    expect(logs.filter((l) => l.includes("signed"))).toEqual([
      `info Bindable Gate: connected; every tool call is reviewed by Bindable in the test context for agent ${AGENT_DID}; every Proposal is signed with agent key ${KEY_ID}.`,
    ]);
    for (const id of ["a", "b"]) await handlers.before_tool_call!({ ...event, toolCallId: id }, ctx);
    const proposals = sent.filter((s) => s.url.endsWith("/v1/reviews"));
    expect(proposals).toHaveLength(2);
    for (const p of proposals) {
      expect(p.body.agent_key_id).toBe(KEY_ID);
      expect(typeof p.body.agent_signature).toBe("string");
      expect(typeof p.body.signed_at).toBe("string");
    }
  });

  it("without one: nothing is signed, and the startup line says so once", async () => {
    const { fetch, sent } = verdict("passed");
    const { handlers, logs } = load({ env: env(), fetch });
    expect(logs.filter((l) => l.includes("signed"))).toEqual([
      `info Bindable Gate: connected; every tool call is reviewed by Bindable in the test context for agent ${AGENT_DID}; nothing is signed, because BINDABLE_AGENT_KEY_FILE is not set.`,
    ]);
    await handlers.before_tool_call!(event, ctx);
    for (const field of ["agent_signature", "agent_key_id", "signed_at"]) expect(sent[0]!.body).not.toHaveProperty(field);
  });

  it("a key file that cannot be read fails closed with a plain message, and sends nothing", async () => {
    const missing = join(mkdtempSync(join(tmpdir(), "gate-openclaw-")), "absent.pem");
    const { fetch, sent } = verdict("passed");
    const { handlers, logs } = load({ env: env({ BINDABLE_AGENT_KEY_FILE: missing, BINDABLE_AGENT_KEY_ID: KEY_ID }), fetch });
    expect(logs).toEqual([
      "error Bindable Gate: configuration invalid, so every tool call is blocked: BINDABLE_AGENT_KEY_FILE could not be read",
    ]);
    expect(await handlers.before_tool_call!(event, ctx)).toEqual({
      block: true,
      blockReason: "Bindable Gate configuration invalid: BINDABLE_AGENT_KEY_FILE could not be read",
    });
    expect(sent).toHaveLength(0);
  });
});

describe("standalone", () => {
  it("allows every call, keeps the local Track, and says so once at startup", async () => {
    const path = join(mkdtempSync(join(tmpdir(), "gate-openclaw-")), "local-track.jsonl");
    const first = load({ env: { BINDABLE_AGENT_ID: AGENT_DID }, localTrackPath: path });
    const second = load({ env: { BINDABLE_AGENT_ID: AGENT_DID }, localTrackPath: path });
    const notices = [...first.logs, ...second.logs].filter((l) => l.includes("recording locally"));
    expect(notices).toHaveLength(1);
    expect(notices[0]).toContain("not connected to Bindable");
    expect(notices[0]).not.toMatch(/Receipt/);
    expect(notices[0]).toContain("Signing: nothing is signed, because BINDABLE_AGENT_KEY_FILE is not set.");
    for (const id of ["a", "b"]) {
      const e = { ...event, toolCallId: id };
      expect(await first.handlers.before_tool_call!(e, ctx)).toBeUndefined();
      await first.handlers.after_tool_call!({ ...e, result: id, durationMs: 1 }, ctx);
    }
    const lines = readFileSync(path, "utf8").trim().split("\n").map((l) => JSON.parse(l));
    expect(lines.map((l) => [l.tool, l.params, l.agent_id])).toEqual([
      ["exec", { command: "ls -la" }, AGENT_DID],
      ["exec", { command: "ls -la" }, AGENT_DID],
    ]);
    expect(verifyLocalTrack(path)).toEqual({ ok: true, lines: 2 });
  });
});

describe("the credential is never logged", () => {
  it("in any line, through release, block, failure and report gaps", async () => {
    const all: string[] = [];
    const leaky = { ...event, params: { command: `curl -H 'x: ${CREDENTIAL}'` } };
    const answers = [
      verdict("passed"),
      verdict("failed"),
      bindable(() => new Error(`connect failed ${CREDENTIAL}`)),
      bindable((s) => (s.url.endsWith("/report") ? new Error(`reset ${CREDENTIAL}`) : { status: 200, body: { verdict: "passed", receipt: { receipt_id: RECEIPT_ID, verdict: "passed" } } })),
    ];
    for (const { fetch, sent } of answers) {
      const { handlers, logs } = load({ env: env(), fetch });
      await handlers.before_tool_call!(leaky, ctx);
      await handlers.after_tool_call!({ ...leaky, result: CREDENTIAL }, ctx);
      await (handlers.gateway_stop as () => Promise<void>)();
      all.push(...logs);
      for (const s of sent) expect(JSON.stringify(s.body)).not.toContain(CREDENTIAL);
    }
    expect(all.some((l) => l.startsWith("warn"))).toBe(true);
    expect(all.join("\n")).not.toContain(CREDENTIAL);
  });
});

describe("a placeholder credential, as inside an OpenShell sandbox", () => {
  const placeholders = [
    "openshell:resolve:env:BINDABLE_GATE_CREDENTIAL",
    "openshell:resolve:env:v3_BINDABLE_GATE_CREDENTIAL",
    `s${"a1".repeat(32)}_BINDABLE_GATE_CREDENTIAL`,
  ];
  for (const placeholder of placeholders) {
    it(`is sent unchanged in the header only, and never logged: ${placeholder.slice(0, 30)}`, async () => {
      const { fetch, sent } = verdict("passed");
      const { handlers, logs } = load({ env: env({ BINDABLE_GATE_CREDENTIAL: placeholder }), fetch });
      const echoing = { ...event, params: { command: `echo ${placeholder}` } };
      expect(await handlers.before_tool_call!(echoing, ctx)).toBeUndefined();
      expect(sent.length).toBeGreaterThan(0);
      for (const s of sent) {
        expect(s.headers.Authorization).toBe(`Bearer ${placeholder}`);
        expect(s.url.startsWith(BASE)).toBe(true);
        expect(s.url).not.toContain(placeholder);
        expect(JSON.stringify(s.body)).not.toContain(placeholder);
        const others = Object.entries(s.headers).filter(([name]) => name !== "Authorization");
        expect(others.some(([, v]) => v.includes(placeholder))).toBe(false);
      }
      expect((sent[0]!.body.payload as { params: unknown }).params).toEqual({ command: "echo [redacted]" });
      expect(logs.join("\n")).not.toContain(placeholder);
      expect(logs.some((l) => l.startsWith("info Bindable Gate: connected"))).toBe(true);
    });
  }
});
