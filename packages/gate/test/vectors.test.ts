/**
 * The vectors the Python and the TypeScript Gate share, in
 * tests/vectors/contract at the repository root. Each side verifies what the
 * other made: the signed bytes, a signature, and a whole local Track.
 */

import { createPublicKey } from "node:crypto";
import { copyFileSync, readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";
import { LocalTrack, proposalSigningBytes, signerFrom, verifyLocalTrack, verifyRecords, verifySignature } from "../src/index.js";
import { p256KeyFile, tempDir } from "./helpers.js";

const VECTORS = join(dirname(fileURLToPath(import.meta.url)), "..", "..", "..", "tests", "vectors", "contract");

interface Vectors {
  input: { persistent_agent_id: string; action: string; nonce: string; signed_at: string; parameters: Record<string, unknown>; target: string | null };
  bytes: string;
  signatures: { made_by: string; public_key_pem: string; agent_signature: string }[];
  local_tracks: { file: string; made_by: string; lines: number; public_key_pem: string }[];
  offline_records: { file: string; made_by: string; records: number; public_key_pem: string }[];
}

const vectors = JSON.parse(readFileSync(join(VECTORS, "contract-vectors.json"), "utf8")) as Vectors;

/** The platform's worked example, with the sixth member, `target`, null, signing the agent's did:webvh DID. */
const SPECIFIED =
  '{"action":"send_email","nonce":"n-0001","parameters":{"attachments":2,"subject":"Weekly report","to":"ops@example.com"},"persistent_agent_id":"did:webvh:QmWv9fYzR4h3TestScid2Gate8Bindab:bindable-ai.com:agent:5b0e6c1a-2f4d-4c8e-9a71-3d2b8f0e4c19","signed_at":"2026-10-26T12:00:00.000Z","target":null}';

describe("the shared vectors", () => {
  it("gives the specified bytes", () => {
    const { input } = vectors;
    const bytes = proposalSigningBytes({
      persistentAgentId: input.persistent_agent_id,
      action: input.action,
      nonce: input.nonce,
      signedAt: input.signed_at,
      parameters: input.parameters,
      target: input.target,
    });
    expect(bytes).toBe(SPECIFIED);
    expect(vectors.bytes).toBe(SPECIFIED);
  });

  it.each(vectors.signatures.map((s) => [s.made_by, s] as const))("verifies the signature %s made", (_by, s) => {
    expect(verifySignature(createPublicKey(s.public_key_pem), SPECIFIED, s.agent_signature)).toBe(true);
    expect(verifySignature(createPublicKey(s.public_key_pem), SPECIFIED.replace("n-0001", "n-0002"), s.agent_signature)).toBe(false);
  });

  it.each(vectors.local_tracks.map((t) => [t.made_by, t] as const))("verifies the local Track %s wrote", (_by, t) => {
    expect(verifyLocalTrack(join(VECTORS, t.file), createPublicKey(t.public_key_pem))).toEqual({ ok: true, lines: t.lines });
  });

  it.each(vectors.local_tracks.map((t) => [t.made_by, t] as const))("extends the local Track %s wrote", (_by, t) => {
    const path = join(tempDir(), "local-track.jsonl");
    copyFileSync(join(VECTORS, t.file), path);
    const key = p256KeyFile();
    new LocalTrack({ path, signer: signerFrom(key.privateKey, "k") }).append({ tool: "x", params: {}, startedAt: new Date().toISOString() });
    expect(verifyLocalTrack(path)).toEqual({ ok: true, lines: t.lines + 1 });
  });

  it.each(vectors.offline_records.map((c) => [c.made_by, c] as const))("verifies the offline records %s wrote", (_by, c) => {
    expect(verifyRecords(join(VECTORS, c.file), createPublicKey(c.public_key_pem))).toEqual({ ok: true, records: c.records });
  });
});
