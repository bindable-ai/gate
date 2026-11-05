/**
 * A Gate credential that is a placeholder, as inside an OpenShell sandbox:
 * the credential is held outside, and the sandbox's proxy replaces the
 * placeholder in the Authorization header of a request to Bindable. The Gate
 * takes it as it takes a credential: no check of its form, sent unchanged in
 * that header and nowhere else, never written to a file, never logged. An
 * empty or missing one still connects nothing.
 */

import { readFileSync, readdirSync, statSync, writeFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";
import { Gate, agentFolder, readConfig, type FetchLike, type ToolCall } from "../src/index.js";
import { AGENT_DID, BASE_URL, RECEIPT_ID, connectedEnv, memoryLogger } from "./helpers.js";

/** The forms OpenShell's resolver gives, for the variable that holds the credential. */
const PLACEHOLDERS = [
  "openshell:resolve:env:BINDABLE_GATE_CREDENTIAL",
  "openshell:resolve:env:v3_BINDABLE_GATE_CREDENTIAL",
  `openshell:resolve:env:s${"a1".repeat(32)}_BINDABLE_GATE_CREDENTIAL`,
  "v3_BINDABLE_GATE_CREDENTIAL",
  `s${"a1".repeat(32)}_BINDABLE_GATE_CREDENTIAL`,
];

interface Wire {
  url: string;
  headers: Record<string, string>;
  body: string;
}

/** A Bindable that can go down, and every request sent to it, whole. */
function bindable() {
  const state = { down: false, wire: [] as Wire[] };
  const fetch: FetchLike = async (url, init) => {
    state.wire.push({ url, headers: { ...(init.headers as Record<string, string>) }, body: String(init.body ?? "") });
    if (state.down) throw new TypeError("fetch failed");
    const respond = (status: number, value: unknown) =>
      new Response(JSON.stringify(value), { status, headers: { "Content-Type": "application/json" } });
    if (url.endsWith("/v1/service-status")) return respond(200, { version: "0.10.1" });
    if (url.endsWith("/v1/account/offline-mode")) return respond(200, { when_unreachable: "automatic" });
    if (url.endsWith("/v1/reviews")) return respond(200, { verdict: "passed", receipt: { receipt_id: RECEIPT_ID, verdict: "passed" } });
    if (url.endsWith("/report")) return respond(201, { trackpoint_id: "tp" });
    if (url.endsWith("/offline-records")) return respond(201, {});
    throw new Error(`the Gate called ${url}`);
  };
  return { fetch, state };
}

function filesUnder(dir: string): string[] {
  return readdirSync(dir).flatMap((name) => {
    const path = join(dir, name);
    return statSync(path).isDirectory() ? filesUnder(path) : [path];
  });
}

let n = 0;
async function runs(gate: Gate, tool: string, params: Record<string, unknown>) {
  n += 1;
  const call: ToolCall = { tool, params, callId: `call-${n}` };
  const decision = await gate.before(call);
  if (decision.outcome === "released") await gate.after(call, { result: { ok: true } });
  return decision;
}

function probeIsDue(): void {
  const path = join(agentFolder(AGENT_DID), "state.json");
  const value = JSON.parse(readFileSync(path, "utf8")) as Record<string, unknown>;
  for (const name of ["next_probe_at", "next_upload_at"]) if (value[name]) value[name] = "2000-01-01T00:00:00.000Z";
  writeFileSync(path, JSON.stringify(value));
}

describe("a placeholder credential", () => {
  for (const placeholder of PLACEHOLDERS) {
    it(`is sent unchanged in the header and nowhere else: ${placeholder.slice(0, 30)}`, async () => {
      const config = readConfig(connectedEnv({ BINDABLE_GATE_CREDENTIAL: placeholder }));
      expect(config.mode === "connected" && config.credential).toBe(placeholder);
      const b = bindable();
      const { lines, logger } = memoryLogger();
      const gate = new Gate(config, { gateVersion: "gate-test/0.1.0", fetch: b.fetch, logger });
      // A tool argument that carries it, as one echoing the environment would.
      const echoed = { command: `echo ${placeholder}`, [placeholder]: "as a key" };

      expect((await runs(gate, "reviewed", echoed)).outcome).toBe("released");
      b.state.down = true;
      expect(await runs(gate, "while_unreachable", echoed)).toMatchObject({ outcome: "released", offline: true });
      b.state.down = false;
      probeIsDue();
      expect((await runs(gate, "reviewed_again", echoed)).outcome).toBe("released");

      const wire = b.state.wire;
      expect(wire.some((w) => w.url.endsWith("/offline-records"))).toBe(true);
      expect(new Set(wire.map((w) => w.headers.Authorization).filter(Boolean))).toEqual(new Set([`Bearer ${placeholder}`]));
      expect(new Set(wire.map((w) => new URL(w.url).host))).toEqual(new Set([new URL(BASE_URL).host]));
      const elsewhere = wire.flatMap((w) => [
        ...(w.url.includes(placeholder) ? [`url ${w.url}`] : []),
        ...(w.body.includes(placeholder) ? [`body ${w.url}`] : []),
        ...Object.entries(w.headers)
          .filter(([name, v]) => name !== "Authorization" && v.includes(placeholder))
          .map(([name]) => `header ${name}`),
      ]);
      expect(elsewhere).toEqual([]);
      const review = JSON.parse(wire.find((w) => w.url.endsWith("/v1/reviews"))!.body) as { payload: Record<string, unknown> };
      expect(review.payload).toEqual({ command: "echo [redacted]", "[redacted]": "as a key" });

      const written = filesUnder(process.env.BINDABLE_OFFLINE_DIR as string);
      expect(written.length).toBeGreaterThan(0);
      expect(written.filter((path) => readFileSync(path, "utf8").includes(placeholder))).toEqual([]);
      expect(lines.join("\n")).not.toContain(placeholder);
    });
  }

  it("empty or missing: not connected, so it is sent nowhere", () => {
    for (const empty of ["", "   "]) {
      expect(readConfig(connectedEnv({ BINDABLE_GATE_CREDENTIAL: empty })).mode).toBe("standalone");
    }
    const { BINDABLE_GATE_CREDENTIAL: _left, ...missing } = connectedEnv();
    expect(readConfig(missing).mode).toBe("standalone");
  });
});
