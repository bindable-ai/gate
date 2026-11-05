/**
 * The platform team's test vector for agent-signed Proposals.
 * tests/vectors/contract/platform-vector.json is the platform's file, copied
 * as published from its main branch (shape in that folder's README). The
 * signature covers the agent's did:webvh DID as persistent_agent_id, and
 * agent_key_id names the key under that DID. The Gate must give its bytes
 * and, signing with its seed key, its signature exactly: RFC 6979 makes the
 * signature deterministic. The Python suite asserts the same, so both
 * languages give the same bytes and the same signature. Nothing is skipped.
 */

import { createHash, createPrivateKey, createPublicKey } from "node:crypto";
import { readFileSync, writeFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";
import { parseAgentId, proposalSigningBytes, readConfig, signProposal, signerFrom, verifySignature, wireAgentId } from "../src/index.js";
import { tempDir } from "./helpers.js";

const FILE = join(dirname(fileURLToPath(import.meta.url)), "..", "..", "..", "tests", "vectors", "contract", "platform-vector.json");

interface PlatformVector {
  private_key_seed: string;
  public_key_jwk: { kty: string; crv: string; x: string; y: string };
  proposal: {
    agent_id: string;
    action_type: string;
    target?: string | null;
    payload: Record<string, unknown>;
    nonce: string;
  };
  signed_at: string;
  agent_key_id: string;
  signing_input: string;
  signing_input_sha256_hex: string;
  agent_signature: string;
}

describe("the platform team's test vector", () => {
  const vector = (): PlatformVector => JSON.parse(readFileSync(FILE, "utf8")) as PlatformVector;
  const did = (v: PlatformVector): string => v.agent_key_id.split("#")[0] as string;
  const publicKey = (v: PlatformVector) => createPublicKey({ key: v.public_key_jwk, format: "jwk" });
  const signingBytes = (v: PlatformVector): string =>
    proposalSigningBytes({
      persistentAgentId: did(v),
      action: v.proposal.action_type,
      nonce: v.proposal.nonce,
      signedAt: v.signed_at,
      parameters: v.proposal.payload,
      target: v.proposal.target ?? null,
    });

  it("gives the platform's bytes", () => {
    const v = vector();
    expect(signingBytes(v)).toBe(v.signing_input);
    expect(createHash("sha256").update(v.signing_input, "utf8").digest("hex")).toBe(v.signing_input_sha256_hex);
  });

  it("verifies the platform's signature", () => {
    const v = vector();
    expect(verifySignature(publicKey(v), signingBytes(v), v.agent_signature)).toBe(true);
  });

  it("signs the agent's did:webvh DID, and its agent_id is what the Gate sends", () => {
    const v = vector();
    const agent = parseAgentId(did(v));
    expect(v.signing_input).toContain(`"persistent_agent_id":"${agent.did}"`);
    // The Agent ID inside the DID until the platform's switch, the DID from then.
    expect([wireAgentId(agent, false), wireAgentId(agent, true)]).toContain(v.proposal.agent_id);
  });

  it("names its key in the form a Gate configuration takes", () => {
    const v = vector();
    const dir = tempDir();
    const path = join(dir, "key.pem");
    const d = createHash("sha256").update(v.private_key_seed, "utf8").digest("base64url");
    writeFileSync(path, createPrivateKey({ key: { ...v.public_key_jwk, d }, format: "jwk" }).export({ type: "pkcs8", format: "pem" }));
    const env = { BINDABLE_AGENT_ID: did(v), BINDABLE_AGENT_KEY_FILE: path, BINDABLE_AGENT_KEY_ID: v.agent_key_id };
    expect(readConfig(env).signer?.keyId).toBe(v.agent_key_id);
  });

  it("reproduces the platform's signature exactly with the vector's key", () => {
    const v = vector();
    const d = createHash("sha256").update(v.private_key_seed, "utf8").digest("base64url");
    const key = createPrivateKey({ key: { ...v.public_key_jwk, d }, format: "jwk" });
    const signed = signProposal(signerFrom(key, v.agent_key_id), {
      persistentAgentId: did(v),
      action: v.proposal.action_type,
      nonce: v.proposal.nonce,
      signedAt: v.signed_at,
      parameters: v.proposal.payload,
      target: v.proposal.target ?? null,
    });
    expect(signed).toEqual({ agent_signature: v.agent_signature, agent_key_id: v.agent_key_id, signed_at: v.signed_at });
    expect(v.agent_signature).toBe("MEQCIGokXMLMZEw8LzYu3SwLgiw8346M1HQG2FJqJUoNwYyZAiA5idiRm7cil2hn1vUWt2qDnpHMFBLTcCEn7sDJgiiC+w==");
  });
});
