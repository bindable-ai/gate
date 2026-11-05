import { execFileSync } from "node:child_process";
import { existsSync, mkdtempSync, readFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";

const dist = fileURLToPath(new URL("../dist/index.js", import.meta.url));

// The archive OpenClaw installs carries only dist/: it must load on its own.
describe.runIf(existsSync(dist))("the bundled plugin", () => {
  it("imports nothing outside Node", () => {
    const code = readFileSync(dist, "utf8");
    const imports = [...code.matchAll(/^import .* from "([^"]+)";?$/gm)].map((m) => m[1]);
    expect(imports.every((name) => name!.startsWith("node:"))).toBe(true);
  });

  it("loads in a bare Node process and registers, standalone, under a temporary home", () => {
    const home = mkdtempSync(join(tmpdir(), "gate-openclaw-home-"));
    const script = `
      const entry = (await import(${JSON.stringify(dist)})).default;
      const seen = []; const logs = [];
      const log = (m) => logs.push(m);
      entry.register({ logger: { info: log, warn: log, error: log }, on: (n, h) => seen.push([n, h]) });
      const before = seen.find(([n]) => n === "before_tool_call")[1];
      const after = seen.find(([n]) => n === "after_tool_call")[1];
      const r = await before({ toolName: "read", params: { path: "a" }, toolCallId: "1" }, { toolName: "read" });
      await after({ toolName: "read", params: { path: "a" }, toolCallId: "1", result: "x" }, { toolName: "read" });
      console.log(JSON.stringify({ id: entry.id, hooks: seen.map(([n]) => n), r: r ?? null, logs }));
    `;
    const out = execFileSync(process.execPath, ["--input-type=module", "-e", script], {
      env: { PATH: process.env.PATH ?? "", HOME: home },
      encoding: "utf8",
    });
    const result = JSON.parse(out.trim().split("\n").pop()!);
    expect(result.id).toBe("bindable-gate");
    expect(result.hooks).toEqual(["before_tool_call", "after_tool_call", "gateway_stop"]);
    expect(result.r).toBeNull();
    expect(result.logs[0]).toContain("recording locally");
    const track = readFileSync(join(home, ".bindable", "local-track.jsonl"), "utf8").trim().split("\n");
    expect(track).toHaveLength(1);
  });
});
