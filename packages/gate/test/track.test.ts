import { readFileSync, writeFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";
import {
  GENESIS_HASH,
  Gate,
  GateConfigError,
  LocalTrack,
  canonicalSha256,
  readConfig,
  verifyLocalTrack,
  type LocalTrackEntry,
} from "../src/index.js";
import { AGENT_DID, KEY_ID, p256KeyFile, tempDir } from "./helpers.js";

const lines = (path: string): LocalTrackEntry[] =>
  readFileSync(path, "utf8").trim().split("\n").map((l) => JSON.parse(l) as LocalTrackEntry);

function standalone(path: string, env: Record<string, string> = {}) {
  return new Gate(readConfig(env), { gateVersion: "gate-test/0.1.0", localTrackPath: path });
}

describe("standalone: every call allowed and kept on the local Track", () => {
  it("allows without any request and appends one line per call", async () => {
    const path = join(tempDir(), "nested", "local-track.jsonl");
    const g = standalone(path, { BINDABLE_AGENT_ID: AGENT_DID });
    expect(g.mode).toBe("standalone");
    for (const [i, tool] of ["read", "exec", "write"].entries()) {
      const call = { tool, params: { n: i }, callId: `c${i}` };
      expect(await g.before(call)).toEqual({ outcome: "released" });
      await g.after(call, { result: { ok: i }, durationMs: 5 });
    }
    const entries = lines(path);
    expect(entries.map((e) => e.tool)).toEqual(["read", "exec", "write"]);
    expect(entries[0]).toMatchObject({
      previous_hash: GENESIS_HASH,
      agent_id: AGENT_DID,
      params: { n: 0 },
      result_summary: { digest: canonicalSha256({ ok: 0 }), preview: '{"ok":0}' },
      error: null,
      duration_ms: 5,
    });
    expect(Object.keys(entries[0]!).sort()).toEqual(
      ["agent_id", "chain_hash", "duration_ms", "error", "finished_at", "id", "params", "previous_hash", "result_summary", "started_at", "tool"],
    );
  });

  it("recomputes the chain, and finds the line that was changed", async () => {
    const path = join(tempDir(), "local-track.jsonl");
    const g = standalone(path);
    for (let i = 0; i < 4; i += 1) {
      await g.before({ tool: "t", params: { i }, callId: `c${i}` });
      await g.after({ tool: "t", params: { i }, callId: `c${i}` }, { result: i });
    }
    const entries = lines(path);
    for (let i = 0; i < entries.length; i += 1) {
      const { chain_hash, agent_signature: _s, ...body } = entries[i]!;
      expect(chain_hash).toBe(canonicalSha256(body));
      expect(body.previous_hash).toBe(i === 0 ? GENESIS_HASH : entries[i - 1]!.chain_hash);
    }
    expect(verifyLocalTrack(path)).toEqual({ ok: true, lines: 4 });

    const tampered = readFileSync(path, "utf8").replace('"params":{"i":2}', '"params":{"i":9}');
    writeFileSync(path, tampered);
    expect(verifyLocalTrack(path)).toMatchObject({ ok: false, brokenAt: 3, reason: "chain_hash does not match" });

    const dropped = readFileSync(path, "utf8").split("\n").filter((_, i) => i !== 1).join("\n");
    writeFileSync(path, dropped);
    expect(verifyLocalTrack(path)).toMatchObject({ ok: false, brokenAt: 2, reason: "previous_hash does not match" });
  });

  it("continues the chain across restarts", async () => {
    const path = join(tempDir(), "local-track.jsonl");
    await standalone(path).after({ tool: "a", params: {} }, { result: 1 });
    await standalone(path).after({ tool: "b", params: {} }, { result: 2 });
    const [first, second] = lines(path);
    expect(second!.previous_hash).toBe(first!.chain_hash);
    expect(verifyLocalTrack(path).ok).toBe(true);
  });

  it("signs every line with the agent key when one is configured", async () => {
    const key = p256KeyFile();
    const path = join(tempDir(), "local-track.jsonl");
    const g = standalone(path, { BINDABLE_AGENT_ID: AGENT_DID, BINDABLE_AGENT_KEY_FILE: key.path, BINDABLE_AGENT_KEY_ID: KEY_ID });
    await g.after({ tool: "exec", params: { command: "ls" } }, { error: "exit 2" });
    const [entry] = lines(path);
    expect(entry).toMatchObject({ agent_key_id: KEY_ID, error: "exit 2", result_summary: null });
    expect(entry!.signed_at).toBe(entry!.finished_at);
    expect(verifyLocalTrack(path, key.publicKey)).toEqual({ ok: true, lines: 1 });
    const other = p256KeyFile();
    expect(verifyLocalTrack(path, other.publicKey)).toMatchObject({ ok: false, reason: "agent_signature does not verify" });
  });

  it("keeps one chain when two writers append to the same file", () => {
    const path = join(tempDir(), "local-track.jsonl");
    const a = new LocalTrack({ path });
    const b = new LocalTrack({ path });
    a.open();
    b.open();
    for (let i = 0; i < 3; i += 1) {
      a.append({ tool: "a", params: { i }, startedAt: new Date().toISOString(), result: i });
      b.append({ tool: "b", params: { i }, startedAt: new Date().toISOString(), result: i });
    }
    expect(verifyLocalTrack(path)).toEqual({ ok: true, lines: 6 });
  });

  it("refuses to extend a file whose last line is not a local Track line", () => {
    const path = join(tempDir(), "local-track.jsonl");
    writeFileSync(path, "not json\n");
    expect(() => new LocalTrack({ path }).open()).toThrow(GateConfigError);
  });
});
