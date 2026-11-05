/**
 * Every test keeps offline mode's files in a folder of its own, never the
 * real `~/.bindable`.
 */

import { createHash } from "node:crypto";
import { mkdirSync, mkdtempSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { beforeEach } from "vitest";

beforeEach(() => {
  process.env.BINDABLE_OFFLINE_DIR = mkdtempSync(join(tmpdir(), "bindable-offline-"));
});

/** The setting the platform last gave this agent, as offline mode keeps it. */
export function keepOfflineSetting(agentId: string, setting: string): void {
  const folder = join(
    process.env.BINDABLE_OFFLINE_DIR as string,
    createHash("sha256").update(agentId, "utf8").digest("hex").slice(0, 32),
  );
  mkdirSync(folder, { recursive: true });
  writeFileSync(join(folder, "setting.json"), JSON.stringify({ when_unreachable: setting, read_at: new Date().toISOString() }));
}
