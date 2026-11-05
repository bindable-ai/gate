/**
 * Offline mode in the TypeScript core: each setting on a simulated outage,
 * the records, their chain, their signatures and their upload. Unreachable is
 * no answer, a timeout or a 5xx; a 4xx and a block never switch; nothing
 * offline ever yields a Receipt.
 */

import { readFileSync, writeFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";
import {
  ASK_MESSAGE,
  ACCEPT_WINDOW_MS,
  STOPPED_MESSAGE,
  DECISION_UNAVAILABLE,
  DecisionUnavailable,
  Gate,
  NOT_REVIEWED,
  OFFLINE_SETTING_NAMES,
  UNREACHABLE_MESSAGE,
  acceptOffline,
  agentFolder,
  isUnreachable,
  keptMessage,
  offlineAgents,
  readConfig,
  reconnectedMessage,
  verifyRecords,
  type FetchLike,
  type ToolCall,
} from "../src/index.js";
import { ACCEPTED_BY_LIMIT } from "../src/offline.js";
import { AGENT_DID, KEY_ID, RECEIPT_ID, connectedEnv, memoryLogger, p256KeyFile } from "./helpers.js";
import { keepOfflineSetting } from "./setup.js";

interface Seen {
  url: string;
  method: string;
  body: Record<string, unknown> | undefined;
  answered: boolean;
}

/** A made-up Bindable that can go down. */
function bindable(options: { setting?: string; verdict?: string } = {}) {
  const state = {
    down: false,
    reviewStatus: 200,
    uploadStatuses: [] as number[],
    seen: [] as Seen[],
  };
  const fetch: FetchLike = async (url, init) => {
    const body = init.body === undefined ? undefined : (JSON.parse(String(init.body)) as Record<string, unknown>);
    state.seen.push({ url, method: String(init.method), body, answered: !state.down });
    if (state.down) throw new TypeError("fetch failed");
    const respond = (status: number, value: unknown) =>
      new Response(JSON.stringify(value), { status, headers: { "Content-Type": "application/json" } });
    if (url.endsWith("/v1/service-status")) return respond(200, { version: "0.10.1" });
    if (url.endsWith("/v1/account/offline-mode")) {
      return options.setting ? respond(200, { when_unreachable: options.setting }) : respond(404, { code: "not_found" });
    }
    if (url.endsWith("/v1/reviews")) {
      if (state.reviewStatus !== 200) return respond(state.reviewStatus, { code: "refused_here" });
      const verdict = options.verdict ?? "passed";
      return respond(200, { verdict, receipt: { receipt_id: RECEIPT_ID, verdict } });
    }
    if (url.endsWith("/report")) return respond(201, { trackpoint_id: "tp" });
    if (url.endsWith("/offline-records")) return respond(state.uploadStatuses.shift() ?? 201, {});
    throw new Error(`offline mode called ${url}`);
  };
  const count = (suffix: string) => state.seen.filter((s) => s.url.endsWith(suffix) && s.answered).length;
  return { fetch, state, count };
}

function gateWith(fetch: FetchLike, extra: Record<string, string> = {}) {
  const { lines, logger } = memoryLogger();
  const gate = new Gate(readConfig(connectedEnv(extra)), { gateVersion: "gate-test/0.1.0", fetch, logger });
  return { gate, lines };
}

const said = (lines: string[]) => lines.filter((l) => l.startsWith("warn Bindable is")).map((l) => l.slice(5));

let n = 0;
function call(tool = "exec"): ToolCall {
  n += 1;
  return { tool, params: { command: "ls" }, callId: `call-${n}` };
}

const folder = () => agentFolder(AGENT_DID);
/** The one chain the test's Gate wrote: `gate-test/0.1.0` is the `gate-test` Gate. */
const chainPath = () => join(folder(), "records-gate-test.jsonl");
const records = (): Record<string, unknown>[] => {
  try {
    return readFileSync(chainPath(), "utf8").split("\n").filter(Boolean).map((l) => JSON.parse(l) as Record<string, unknown>);
  } catch {
    return [];
  }
};
const RECORD_FIELDS = new Set([
  "id", "previous_hash", "chain_hash", "agent_id", "tool", "params_digest", "result_digest", "failed", "duration_ms",
  "started_at", "finished_at", "offline_since", "allowed_by", "asked_at", "accepted_by", "agent_key_id", "signed_at", "agent_signature",
]);
const state = (): Record<string, unknown> => {
  try {
    return JSON.parse(readFileSync(join(folder(), "state.json"), "utf8")) as Record<string, unknown>;
  } catch {
    return {};
  }
};
function probeIsDue(): void {
  const value = state();
  for (const name of ["next_probe_at", "next_upload_at"]) if (value[name]) value[name] = "2000-01-01T00:00:00.000Z";
  writeFileSync(join(folder(), "state.json"), JSON.stringify(value));
}

/** One call that runs, start to finish. */
/** The outage began this long ago: time passed with nobody at the machine. */
function outageBegan(secondsAgo: number): void {
  const path = join(folder(), "state.json");
  const value = JSON.parse(readFileSync(path, "utf8")) as Record<string, unknown>;
  if (!value.offline) throw new Error("only an outage has a start to move");
  value.offline_since = new Date(Date.now() - secondsAgo * 1000).toISOString();
  writeFileSync(path, JSON.stringify(value));
}

async function runs(gate: Gate, tool = "exec") {
  const c = call(tool);
  const decision = await gate.before(c);
  if (decision.outcome === "released") await gate.after(c, { result: { ok: true } });
  return decision;
}

describe("the words, exactly", () => {
  it("are the ones given", () => {
    expect(ASK_MESSAGE).toBe(
      "Bindable is unreachable. This agent's actions are not being reviewed. To continue anyway run: bindable-gate offline accept",
    );
    expect(UNREACHABLE_MESSAGE).toBe("Bindable is unreachable. This agent's actions are not being reviewed.");
    expect(NOT_REVIEWED).toBe("Not reviewed: ran while Bindable was unreachable");
    expect(reconnectedMessage(3)).toBe(
      "Bindable is reachable and your agent's actions are being reviewed again. 3 offline records sent to the Track.",
    );
    expect(STOPPED_MESSAGE).toBe(
      "Bindable is unreachable and nobody ran bindable-gate offline accept within 10 minutes, so this action was stopped. This agent stays stopped until Bindable is reachable again.",
    );
    expect(ACCEPT_WINDOW_MS).toBe(600_000);
    expect(reconnectedMessage(1)).toBe(
      "Bindable is reachable and your agent's actions are being reviewed again. 1 offline record sent to the Track.",
    );
    expect(keptMessage(1)).toMatch(/ 1 offline record is kept on this machine and will be sent to the Track later\.$/);
    expect(keptMessage(2)).toMatch(/ 2 offline records are kept on this machine and will be sent to the Track later\.$/);
    expect(OFFLINE_SETTING_NAMES).toEqual({
      automatic: "switch automatically",
      ask_first: "ask me first",
      stop: "stop the agent",
    });
  });
});

describe("what counts as unreachable", () => {
  it("no answer, a timeout or a 5xx, and nothing else", () => {
    expect(isUnreachable(new DecisionUnavailable("x", undefined, undefined, true))).toBe(true);
    expect(isUnreachable(undefined, true)).toBe(true);
    for (const status of [500, 502, 503, 504]) expect(isUnreachable(new DecisionUnavailable("x", status))).toBe(true);
    for (const status of [400, 401, 403, 404, 409, 422, 429]) expect(isUnreachable(new DecisionUnavailable("x", status))).toBe(false);
    expect(isUnreachable(new DecisionUnavailable("the review could not be read", 200))).toBe(false);
    expect(isUnreachable(new Error("anything else"))).toBe(false);
  });
});

describe("each setting on an outage", () => {
  it("with nothing kept, ask me first: blocked with the person's words", async () => {
    const b = bindable();
    b.state.down = true;
    const { gate, lines } = gateWith(b.fetch);
    expect(await gate.before(call())).toEqual({ outcome: "blocked", reason: ASK_MESSAGE, unavailable: true });
    expect(said(lines)).toEqual([ASK_MESSAGE]);
    expect(records()).toEqual([]);
  });

  it("ask me first, accepted within 10 minutes: runs", async () => {
    const b = bindable({ setting: "ask_first" });
    const { gate } = gateWith(b.fetch);
    await runs(gate, "warm_up");
    b.state.down = true;
    expect(await gate.before(call())).toEqual({ outcome: "blocked", reason: ASK_MESSAGE, unavailable: true });
    outageBegan(9 * 60 + 50);
    const accepted = await acceptOffline(AGENT_DID, { by: "person@this-machine" });
    expect(await runs(gate)).toEqual({ outcome: "released", offline: true });
    expect(records()).toMatchObject([{ allowed_by: "ask_first", asked_at: accepted.acceptedAt }]);
  });

  it("ask me first, nobody accepts in 10 minutes: the agent stops", async () => {
    const b = bindable({ setting: "ask_first" });
    const { gate, lines } = gateWith(b.fetch);
    await runs(gate, "warm_up");
    b.state.down = true;
    expect((await gate.before(call())).outcome).toBe("blocked");
    outageBegan(10 * 60);
    expect(await gate.before(call())).toEqual({ outcome: "blocked", reason: STOPPED_MESSAGE, unavailable: true });
    expect(said(lines).at(-1)).toBe(STOPPED_MESSAGE);
    expect(offlineAgents()[0]).toMatchObject({ waiting: true, stopped: true });
    await expect(acceptOffline(AGENT_DID, { by: "person@this-machine" })).rejects.toThrow(/nobody accepted within 10 minutes/);
    expect((await gate.before(call())).outcome).toBe("blocked");
    expect(records()).toEqual([]);
    b.state.down = false;
    probeIsDue();
    expect(await runs(gate, "reviewed_again")).toMatchObject({ outcome: "released" });
    expect(b.count("/v1/reviews")).toBe(2);
  });

  it("cuts who accepted to what Bindable takes", async () => {
    const b = bindable({ setting: "ask_first" });
    const { gate } = gateWith(b.fetch);
    await runs(gate, "warm_up");
    b.state.down = true;
    expect((await gate.before(call())).outcome).toBe("blocked");
    await acceptOffline(AGENT_DID, { by: `${"p".repeat(150)}@host` });
    await runs(gate);
    const [record] = records();
    expect(record!.accepted_by).toBe("p".repeat(100));
    expect(ACCEPTED_BY_LIMIT).toBe(100);
  });

  it("ask me first runs once accepted, and records who accepted and when", async () => {
    const b = bindable({ setting: "ask_first" });
    const { gate, lines } = gateWith(b.fetch);
    await runs(gate, "warm_up");
    b.state.down = true;
    expect((await gate.before(call())).outcome).toBe("blocked");
    const accepted = await acceptOffline(AGENT_DID, { by: "person@this-machine" });
    expect(await runs(gate)).toEqual({ outcome: "released", offline: true });
    const [record] = records();
    expect(record).toMatchObject({ allowed_by: "ask_first", asked_at: accepted.acceptedAt, accepted_by: "person@this-machine", tool: "exec" });
    expect(accepted.acceptedBy).toBe("person@this-machine");
    const kept = readFileSync(join(folder(), "accepts.jsonl"), "utf8").trim();
    expect(JSON.parse(kept)).toEqual({ agent_id: AGENT_DID, offline_since: record!.offline_since, accepted_at: accepted.acceptedAt, accepted_by: "person@this-machine" });
    expect(said(lines)).toEqual([ASK_MESSAGE, UNREACHABLE_MESSAGE]);
  });

  it("switch automatically runs and says so once", async () => {
    const b = bindable({ setting: "automatic" });
    const { gate, lines } = gateWith(b.fetch);
    await runs(gate, "warm_up");
    b.state.down = true;
    expect(await runs(gate, "one")).toEqual({ outcome: "released", offline: true });
    expect(await runs(gate, "two")).toEqual({ outcome: "released", offline: true });
    expect(said(lines)).toEqual([UNREACHABLE_MESSAGE]);
    expect(records().map((r) => r.tool)).toEqual(["one", "two"]);
    expect(records().every((r) => r.allowed_by === "automatic" && !("accepted_by" in r) && Object.keys(r).every((k) => RECORD_FIELDS.has(k)))).toBe(true);
    expect(JSON.stringify(records())).not.toContain("command");
  });

  it("stop the agent is the fail-closed answer, unchanged", async () => {
    keepOfflineSetting(AGENT_DID, "stop");
    const b = bindable();
    b.state.down = true;
    const { gate, lines } = gateWith(b.fetch);
    expect(await gate.before(call())).toEqual({ outcome: "blocked", reason: DECISION_UNAVAILABLE, unavailable: true });
    expect(said(lines)).toEqual([]);
    expect(state().offline).toBeUndefined();
  });

  it("a 5xx switches", async () => {
    for (const status of [500, 503]) {
      keepOfflineSetting(AGENT_DID, "automatic");
      const b = bindable();
      b.state.reviewStatus = status;
      const { gate } = gateWith(b.fetch);
      expect(await gate.before(call())).toEqual({ outcome: "released", offline: true });
    }
  });

  it("a 4xx never switches, whatever the setting", async () => {
    keepOfflineSetting(AGENT_DID, "automatic");
    for (const status of [400, 401, 403, 404, 409, 422]) {
      const b = bindable();
      b.state.reviewStatus = status;
      const { gate } = gateWith(b.fetch);
      expect(await gate.before(call())).toEqual({ outcome: "blocked", reason: DECISION_UNAVAILABLE, unavailable: true });
    }
    expect(records()).toEqual([]);
    expect(state().offline).toBeUndefined();
  });

  it("a block never switches", async () => {
    keepOfflineSetting(AGENT_DID, "automatic");
    const b = bindable({ verdict: "failed" });
    const { gate } = gateWith(b.fetch);
    expect((await gate.before(call())).outcome).toBe("blocked");
    expect(records()).toEqual([]);
  });

  it("a timeout switches", async () => {
    keepOfflineSetting(AGENT_DID, "automatic");
    const hang: FetchLike = (_url, init) =>
      new Promise<Response>((_resolve, reject) => init.signal?.addEventListener("abort", () => reject(new DOMException("aborted", "AbortError"))));
    const gate = new Gate(readConfig(connectedEnv()), { gateVersion: "gate-test/0.1.0", fetch: hang, deadlineMs: 200 });
    expect(await gate.before(call())).toEqual({ outcome: "released", offline: true });
  });
});

describe("the records", () => {
  async function offlineRun(count: number, extra: Record<string, string> = {}) {
    const b = bindable({ setting: "automatic" });
    const { gate } = gateWith(b.fetch, extra);
    await runs(gate, "warm_up");
    b.state.down = true;
    for (let i = 0; i < count; i += 1) await runs(gate, `action_${i}`);
    return b;
  }
  const path = chainPath;

  it("chain, and verify", async () => {
    await offlineRun(3);
    expect(verifyRecords(path())).toEqual({ ok: true, records: 3 });
    const chain = records();
    expect(chain[0]!.previous_hash).toBe("0".repeat(64));
    expect(chain.slice(1).map((r) => r.previous_hash)).toEqual(chain.slice(0, -1).map((r) => r.chain_hash));
  });

  for (const tamper of ["edit", "delete", "reorder", "receipt"] as const) {
    it(`detect tampering: ${tamper}`, async () => {
      await offlineRun(3);
      const lines = readFileSync(path(), "utf8").split("\n").filter(Boolean);
      if (tamper === "edit") lines[1] = JSON.stringify({ ...JSON.parse(lines[1]!), tool: "rm" });
      if (tamper === "delete") lines.splice(1, 1);
      if (tamper === "reorder") [lines[1], lines[2]] = [lines[2]!, lines[1]!];
      if (tamper === "receipt") lines[1] = JSON.stringify({ ...JSON.parse(lines[1]!), receipt_id: "made-up" });
      writeFileSync(path(), lines.join("\n") + "\n");
      expect(verifyRecords(path())).toMatchObject({ ok: false, brokenAt: 2 });
    });
  }

  it("signed with the agent key, verify with it, and not with another", async () => {
    const key = p256KeyFile();
    await offlineRun(2, { BINDABLE_AGENT_KEY_FILE: key.path, BINDABLE_AGENT_KEY_ID: KEY_ID });
    expect(records().every((r) => r.agent_key_id === KEY_ID && typeof r.agent_signature === "string")).toBe(true);
    expect(verifyRecords(path(), key.publicKey)).toEqual({ ok: true, records: 2 });
    expect(verifyRecords(path(), p256KeyFile().publicKey)).toMatchObject({ ok: false, reason: "agent_signature does not verify" });
  });

  it("yield no Receipt, and nothing is reviewed or reported offline", async () => {
    const b = await offlineRun(3);
    expect(b.count("/v1/reviews")).toBe(1);
    expect(b.count("/report")).toBe(1);
    for (const record of records()) {
      expect(record).not.toHaveProperty("receipt_id");
      expect(record).not.toHaveProperty("verdict");
    }
  });
});

describe("reconnecting", () => {
  it("sends the records, says so, and reviews again", async () => {
    const b = bindable({ setting: "automatic" });
    const { gate, lines } = gateWith(b.fetch);
    await runs(gate, "warm_up");
    b.state.down = true;
    await runs(gate, "one");
    await runs(gate, "two");
    b.state.down = false;
    probeIsDue();
    const back = await gate.before(call("three"));
    expect(back).toEqual({ outcome: "released", receiptId: RECEIPT_ID });
    expect(said(lines)).toEqual([UNREACHABLE_MESSAGE, reconnectedMessage(2)]);
    const upload = b.state.seen.find((s) => s.url.endsWith("/offline-records"))!;
    expect(upload.url).toContain(`/v1/agents/${AGENT_DID}/offline-records`);
    expect((upload.body!.records as unknown[]).length).toBe(2);
    expect(upload.body!.records).toEqual(records());
    expect(upload.body!.offline_period).toEqual({ started_at: records()[0]!.offline_since, ended_at: expect.any(String) });
    expect(state()).toMatchObject({ offline: false, sent: { "gate-test": 2 } });
  });

  it("keeps the records when the upload fails, and retries", async () => {
    const b = bindable({ setting: "automatic" });
    b.state.uploadStatuses = [503, 404];
    const { gate, lines } = gateWith(b.fetch);
    await runs(gate, "warm_up");
    b.state.down = true;
    await runs(gate, "one");
    b.state.down = false;
    probeIsDue();
    expect((await runs(gate, "two")).outcome).toBe("released");
    expect(said(lines).at(-1)).toBe(keptMessage(1));
    probeIsDue();
    await runs(gate, "three");
    probeIsDue();
    await runs(gate, "four");
    expect(said(lines).at(-1)).toBe(reconnectedMessage(1));
    expect(b.state.seen.filter((s) => s.url.endsWith("/offline-records"))).toHaveLength(3);
    expect(records()).toHaveLength(1);
    expect(state().sent).toEqual({ "gate-test": 1 });
  });
});

describe("accepting", () => {
  it("says so when nothing is waiting, and lists the agents", async () => {
    await expect(acceptOffline()).rejects.toThrow("there is nothing to accept");
    const b = bindable();
    b.state.down = true;
    const { gate } = gateWith(b.fetch);
    await gate.before(call());
    expect(offlineAgents()).toMatchObject([{ agentId: AGENT_DID, offline: true, setting: "ask_first", waiting: true }]);
  });
});
