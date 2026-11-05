import { describe, expect, it } from "vitest";
import {
  DECISION_UNAVAILABLE,
  Gate,
  REPORT_GATE_KIND,
  SEND_AGENT_DID,
  canonicalSha256,
  parseAgentId,
  proposalSigningBytes,
  readConfig,
  signerFrom,
  signingNotice,
  verifySignature,
  wireAgentId,
} from "../src/index.js";
import {
  AGENT_DID,
  AGENT_UUID,
  BASE_URL,
  CREDENTIAL,
  DECIDED_AT,
  KEY_ID,
  RECEIPT_ID,
  connectedEnv,
  fakeFetch,
  memoryLogger,
  p256KeyFile,
  platform,
} from "./helpers.js";
import { keepOfflineSetting } from "./setup.js";

const VERSION = "gate-test/0.1.0";
const call = { tool: "exec", params: { command: "ls" }, payload: { params: { command: "ls" } }, callId: "call-1" };

function gate(fetch: ReturnType<typeof fakeFetch>["fetch"], env = connectedEnv(), extra = {}) {
  return new Gate(readConfig(env), { gateVersion: VERSION, fetch, ...extra });
}

describe("before: Released", () => {
  it("releases on passed, sending the Proposal the contract names", async () => {
    const { fetch, sent } = platform("passed");
    const decision = await gate(fetch).before(call);
    expect(decision).toEqual({ outcome: "released", receiptId: RECEIPT_ID });
    expect(sent).toHaveLength(1);
    const [review] = sent;
    expect(review!.url).toBe(`${BASE_URL}/v1/reviews`);
    expect(review!.method).toBe("POST");
    expect(review!.headers).toEqual({ Authorization: `Bearer ${CREDENTIAL}`, "Content-Type": "application/json" });
    expect(Object.keys(review!.body).sort()).toEqual(
      ["action_type", "agent_id", "nonce", "payload", "proposed_action_id", "proposed_at", "review_context"],
    );
    expect(review!.body).toMatchObject({
      agent_id: AGENT_DID,
      action_type: "exec",
      payload: { params: { command: "ls" } },
      review_context: "test",
    });
    expect(review!.body.nonce).toMatch(/^[0-9a-f]{32}$/);
    expect(review!.body.proposed_at).toMatch(/^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\.\d{3}Z$/);
  });

  it("does not release when the Receipt's verdict disagrees", async () => {
    const { fetch } = fakeFetch(() => ({ status: 200, body: { verdict: "passed", receipt: { receipt_id: RECEIPT_ID, verdict: "failed" } } }));
    const decision = await gate(fetch).before(call);
    expect(decision.outcome).toBe("blocked");
  });
});

describe("before: Blocked", () => {
  it("blocks with Bindable's reason and reports blocked at once", async () => {
    const { fetch, sent } = platform("failed", { failed_rule: "no-shell", reason_detail: "Shell commands need approval." });
    const g = gate(fetch);
    const decision = await g.before(call);
    expect(decision).toEqual({
      outcome: "blocked",
      reason: `Action Gate blocked exec. Action Review returned failed under the rule no-shell. Shell commands need approval. Receipt ${RECEIPT_ID}.`,
      receiptId: RECEIPT_ID,
      unavailable: false,
    });
    await g.flush();
    const report = sent[1]!;
    expect(report.url).toBe(`${BASE_URL}/v1/receipts/${RECEIPT_ID}/report`);
    expect(report.body).toMatchObject({
      outcome: "blocked",
      gate_kind: REPORT_GATE_KIND,
      gate_version: VERSION,
      action_result_digest: null,
      proposed_at: sent[0]!.body.proposed_at,
    });
    expect(Object.keys(report.body).sort()).toEqual(
      ["action_result_digest", "enforced_at", "gate_kind", "gate_version", "outcome", "proposed_at"],
    );
  });

  it("reports gate_kind runtime_plugin", () => {
    expect(REPORT_GATE_KIND).toBe("runtime_plugin");
  });
});

describe("before: fails closed", () => {
  const cases: Array<[string, Parameters<typeof fakeFetch>[0]]> = [
    ["unreachable", () => new TypeError("fetch failed")],
    ["500", () => ({ status: 500, body: { code: "internal" } })],
    ["401", () => ({ status: 401, body: { code: "credential_invalid" } })],
    ["422", () => ({ status: 422, body: { code: "request_invalid" } })],
    ["200 with no Receipt", () => ({ status: 200, body: { result_code: "exception", reason_code: "x" } })],
    ["200 not JSON", () => ({ status: 200 })],
  ];
  for (const [name, answer] of cases) {
    it(`blocks with "decision unavailable" when ${name}, under stop the agent`, async () => {
      keepOfflineSetting(AGENT_DID, "stop");
      const { fetch } = fakeFetch(answer);
      expect(await gate(fetch).before(call)).toEqual({ outcome: "blocked", reason: DECISION_UNAVAILABLE, unavailable: true });
    });
  }

  it("retries a transport failure once with the same Proposal", async () => {
    let n = 0;
    const { fetch, sent } = fakeFetch(() => (n++ === 0 ? new TypeError("fetch failed") : { status: 200, body: { verdict: "passed", receipt: { receipt_id: RECEIPT_ID, verdict: "passed" } } }));
    expect((await gate(fetch).before(call)).outcome).toBe("released");
    expect(sent[1]!.body).toEqual(sent[0]!.body);
  });

  it("blocks within its deadline when Bindable never answers, under stop the agent", async () => {
    keepOfflineSetting(AGENT_DID, "stop");
    const { fetch } = fakeFetch(() => "hang");
    const started = Date.now();
    const decision = await gate(fetch, connectedEnv(), { deadlineMs: 200 }).before(call);
    expect(decision).toEqual({ outcome: "blocked", reason: DECISION_UNAVAILABLE, unavailable: true });
    expect(Date.now() - started).toBeLessThan(2_000);
  });
});

describe("after: the enforcement outcome", () => {
  it("reports released with the result's digest", async () => {
    const { fetch, sent } = platform("passed");
    const g = gate(fetch);
    await g.before(call);
    await g.after(call, { result: { content: [{ type: "text", text: "a b" }] }, durationMs: 12 });
    const report = sent[1]!;
    expect(report.url).toBe(`${BASE_URL}/v1/receipts/${RECEIPT_ID}/report`);
    expect(report.headers.Authorization).toBe(`Bearer ${CREDENTIAL}`);
    expect(report.body).toMatchObject({
      outcome: "released",
      gate_kind: "runtime_plugin",
      gate_version: VERSION,
      action_result_digest: canonicalSha256({ content: [{ type: "text", text: "a b" }] }),
      proposed_at: sent[0]!.body.proposed_at,
    });
    expect(Date.parse(String(report.body.enforced_at))).toBeGreaterThanOrEqual(Date.parse(DECIDED_AT));
  });

  it("reports released with no digest when the tool failed while running", async () => {
    const { fetch, sent } = platform("passed");
    const g = gate(fetch);
    await g.before(call);
    await g.after(call, { error: "exit 1", durationMs: 3 });
    expect(sent[1]!.body).toMatchObject({ outcome: "released", action_result_digest: null });
  });

  it("reports nothing more for a call it blocked", async () => {
    const { fetch, sent } = platform("failed");
    const g = gate(fetch);
    await g.before(call);
    await g.flush();
    await g.after(call, { error: "blocked" });
    expect(sent.filter((s) => s.url.endsWith("/report"))).toHaveLength(1);
  });

  it("logs a gap, and sends nothing, for a call it holds no Receipt for", async () => {
    const { fetch, sent } = platform("passed");
    const { lines, logger } = memoryLogger();
    await gate(fetch, connectedEnv(), { logger }).after({ ...call, callId: "unknown" }, { result: 1 });
    expect(sent).toHaveLength(0);
    expect(lines.join("\n")).toContain("report gap for exec");
  });

  it("a report that fails never throws", async () => {
    const { fetch } = fakeFetch((s) => (s.url.endsWith("/report") ? new TypeError("fetch failed") : { status: 200, body: { verdict: "passed", receipt: { receipt_id: RECEIPT_ID, verdict: "passed" } } }));
    const g = gate(fetch);
    await g.before(call);
    await expect(g.after(call, { result: "ok" })).resolves.toBeUndefined();
  });
});

describe("signed and unsigned Proposals", () => {
  it("unsigned: no key configured, the Proposal is exactly as before", async () => {
    const { fetch, sent } = platform("passed");
    await gate(fetch).before(call);
    for (const field of ["agent_signature", "agent_key_id", "signed_at"]) expect(sent[0]!.body).not.toHaveProperty(field);
  });

  it("the wire's agent_id is the DID, by one constant", () => {
    expect(SEND_AGENT_DID).toBe(true);
    const agent = parseAgentId(AGENT_DID);
    expect(wireAgentId(agent)).toBe(AGENT_DID);
    expect(wireAgentId(agent, false)).toBe(AGENT_UUID);
  });

  it("signed: every Proposal carries a signature over the RFC 8785 bytes", async () => {
    const key = p256KeyFile();
    const { fetch, sent } = platform("passed");
    await gate(fetch, connectedEnv({ BINDABLE_AGENT_KEY_FILE: key.path, BINDABLE_AGENT_KEY_ID: KEY_ID })).before(call);
    const body = sent[0]!.body;
    expect(body.agent_key_id).toBe(KEY_ID);
    expect(body.signed_at).toMatch(/Z$/);
    const bytes = proposalSigningBytes({
      persistentAgentId: AGENT_DID,
      action: "exec",
      nonce: String(body.nonce),
      signedAt: String(body.signed_at),
      parameters: body.payload as Record<string, unknown>,
      target: null,
    });
    expect(bytes.startsWith('{"action":"exec","nonce":')).toBe(true);
    // The signature covers the configured did:webvh DID itself, never a did:web name.
    expect(bytes).toContain(`"persistent_agent_id":"${AGENT_DID}"`);
    expect(bytes).not.toContain("did:web:");
    expect(verifySignature(key.publicKey, bytes, String(body.agent_signature))).toBe(true);
    expect(verifySignature(key.publicKey, bytes.replace("exec", "exed"), String(body.agent_signature))).toBe(false);
  });

  it("unsigned without a key: none of the three fields is sent", async () => {
    const { fetch, sent } = platform("passed");
    await gate(fetch).before(call);
    for (const field of ["agent_signature", "agent_key_id", "signed_at"]) expect(sent[0]!.body).not.toHaveProperty(field);
  });

  it("a signature that cannot be made blocks the call, and nothing is sent unsigned", async () => {
    const key = p256KeyFile();
    const config = readConfig(connectedEnv({ BINDABLE_AGENT_KEY_FILE: key.path, BINDABLE_AGENT_KEY_ID: KEY_ID }));
    const broken = { ...config.signer!, sign: (): string => { throw new Error("no signature"); } };
    const { fetch, sent } = platform("passed");
    const decision = await new Gate({ ...config, signer: broken }, { gateVersion: VERSION, fetch }).before(call);
    expect(decision).toMatchObject({ outcome: "blocked", reason: DECISION_UNAVAILABLE });
    expect(sent).toHaveLength(0);
  });

  it("signs deterministically: the same key and bytes give the same signature (RFC 6979)", () => {
    const key = p256KeyFile();
    const signer = signerFrom(key.privateKey, KEY_ID);
    const bytes = proposalSigningBytes({ persistentAgentId: AGENT_DID, action: "a", nonce: "n", signedAt: "2026-10-31T12:00:00.000Z", parameters: {}, target: null });
    expect(signer.sign(bytes)).toBe(signer.sign(bytes));
    expect(verifySignature(key.publicKey, bytes, signer.sign(bytes))).toBe(true);
  });

  it("says once at startup whether it signs", () => {
    const key = p256KeyFile();
    const signer = readConfig(connectedEnv({ BINDABLE_AGENT_KEY_FILE: key.path, BINDABLE_AGENT_KEY_ID: KEY_ID })).signer;
    expect(signingNotice(signer, "connected")).toBe(`every Proposal is signed with agent key ${KEY_ID}`);
    expect(signingNotice(undefined, "connected")).toBe("nothing is signed, because BINDABLE_AGENT_KEY_FILE is not set");
  });

  it("the signed bytes are RFC 8785: six members ordered by name, no whitespace", () => {
    const fields = {
      persistentAgentId: AGENT_DID,
      action: "write",
      nonce: "n1",
      signedAt: "2026-10-26T12:00:00.000Z",
      parameters: { z: 1, a: [true, null, "é"], m: { y: 2.5, b: 1e21 } },
    };
    expect(proposalSigningBytes({ ...fields, target: null })).toBe(
      `{"action":"write","nonce":"n1","parameters":{"a":[true,null,"é"],"m":{"b":1e+21,"y":2.5},"z":1},"persistent_agent_id":"did:webvh:QmWv9fYzR4h3TestScid2Gate8Bindab:bindable.example:agent:9b0c2f4e-1d2a-4c3b-8e5f-6a7b8c9d0e1f","signed_at":"2026-10-26T12:00:00.000Z","target":null}`,
    );
    expect(proposalSigningBytes({ ...fields, target: "repo:acme/web" })).toMatch(/,"target":"repo:acme\/web"}$/);
  });

  it("a signature does not survive a change of target", () => {
    const key = p256KeyFile();
    const fields = { persistentAgentId: AGENT_DID, action: "send", nonce: "n", signedAt: "2026-10-26T12:00:00.000Z", parameters: {} };
    const bytes = proposalSigningBytes({ ...fields, target: null });
    const signature = signerFrom(key.privateKey, KEY_ID).sign(bytes);
    expect(verifySignature(key.publicKey, bytes, signature)).toBe(true);
    expect(verifySignature(key.publicKey, proposalSigningBytes({ ...fields, target: "elsewhere" }), signature)).toBe(false);
  });
});

describe("the credential is never logged or sent on", () => {
  it("is masked in the payload and absent from every log line", async () => {
    const { lines, logger } = memoryLogger();
    const leaky = { tool: "exec", params: { command: `echo ${CREDENTIAL}` }, callId: "c" };
    const { fetch, sent } = fakeFetch((s) =>
      s.url.endsWith("/report") ? new Error(`socket closed after ${CREDENTIAL}`) : { status: 200, body: { verdict: "passed", receipt: { receipt_id: RECEIPT_ID, verdict: "passed" } } },
    );
    const g = gate(fetch, connectedEnv(), { logger });
    await g.before(leaky);
    await g.after(leaky, { result: CREDENTIAL });
    expect(JSON.stringify(sent[0]!.body)).not.toContain(CREDENTIAL);
    expect((sent[0]!.body.payload as { command: string }).command).toBe("echo [redacted]");
    expect(lines.length).toBeGreaterThan(0);
    expect(lines.join("\n")).not.toContain(CREDENTIAL);
  });

  it("is absent from every log line when the review fails", async () => {
    const { lines, logger } = memoryLogger();
    const { fetch } = fakeFetch(() => new Error(`connect ECONNREFUSED ${CREDENTIAL}`));
    await gate(fetch, connectedEnv(), { logger }).before(call);
    expect(lines.join("\n")).not.toContain(CREDENTIAL);
  });
});
