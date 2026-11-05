/**
 * The agent key, and the signature a Proposal carries.
 *
 * The signed bytes are the RFC 8785 canonical JSON of an object with six
 * members: `action`, `nonce`, `parameters`, `persistent_agent_id`,
 * `signed_at` and `target`. RFC 8785 orders members by their names, so the
 * bytes are the same whatever order they are written in. `target` is the
 * Proposal's target, and `null` when the Proposal has none, so a target cannot
 * be added or changed after signing. The signature is ECDSA P-256 over
 * SHA-256 of those bytes, DER-encoded, then base64: the encoding the Bindable
 * verifier reads. The nonce is RFC 6979's, so the same key and bytes always
 * give the same signature, the one the Python Gate and the platform give; `s`
 * is left as computed, as OpenSSL leaves it. Node has no deterministic ECDSA,
 * so the signing is @noble/curves'; loading and verifying stay Node's.
 */

import { createPrivateKey, createPublicKey, verify, type KeyObject } from "node:crypto";
import { readFileSync } from "node:fs";
import { p256 } from "@noble/curves/nist.js";
import { canonicalJson } from "./canonical.js";
import { GateConfigError } from "./errors.js";

/** An agent key, loaded and checked. */
export interface Signer {
  /** The id a verifier looks the key up by: `<did>#<key uuid>`. */
  readonly keyId: string;
  /** Base64 DER ECDSA P-256 / SHA-256 signature over `bytes`. */
  sign(bytes: string): string;
  /** The public half, for whoever checks a signature. */
  readonly publicKey: KeyObject;
}

/** What a signed Proposal adds. */
export interface ProposalSignature {
  agent_signature: string;
  agent_key_id: string;
  signed_at: string;
}

/** What the signature covers. */
export interface SignedProposalFields {
  persistentAgentId: string;
  action: string;
  nonce: string;
  signedAt: string;
  parameters: Record<string, unknown>;
  /** The Proposal's target; `null` when it has none. */
  target: string | null;
}

/**
 * Load a PEM private key from `path` and check it is ECDSA P-256. Reads the
 * file synchronously: OpenClaw registers plugins synchronously, and a key that
 * cannot be used must be known before the first tool call.
 *
 * @throws GateConfigError naming the setting, never the key or the file's contents.
 */
export function loadSigner(path: string, keyId: string): Signer {
  let pem: string;
  try {
    pem = readFileSync(path, "utf8");
  } catch {
    throw new GateConfigError("BINDABLE_AGENT_KEY_FILE could not be read");
  }
  let key: KeyObject;
  try {
    key = createPrivateKey({ key: pem, format: "pem" });
  } catch {
    throw new GateConfigError("BINDABLE_AGENT_KEY_FILE is not a PEM private key");
  }
  return signerFrom(key, keyId);
}

/** A signer from a private key already loaded. */
export function signerFrom(key: KeyObject, keyId: string): Signer {
  if (
    key.type !== "private" ||
    key.asymmetricKeyType !== "ec" ||
    key.asymmetricKeyDetails?.namedCurve !== "prime256v1"
  ) {
    throw new GateConfigError("BINDABLE_AGENT_KEY_FILE is not an ECDSA P-256 private key");
  }
  if (!keyId) throw new GateConfigError("the agent key has no key id");
  const publicKey = createPublicKey(key);
  const secret = Buffer.from(key.export({ format: "jwk" }).d as string, "base64url");
  return {
    keyId,
    publicKey,
    sign(bytes: string): string {
      const der = p256.sign(Buffer.from(bytes, "utf8"), secret, { prehash: true, lowS: false, format: "der" });
      return Buffer.from(der).toString("base64");
    },
  };
}

/**
 * What the Gate's startup line says about signing. With a key, every Proposal
 * (connected) or every local Track line (standalone) is signed; without one,
 * nothing is.
 */
export function signingNotice(signer: Signer | undefined, mode: "connected" | "standalone"): string {
  if (!signer) return "nothing is signed, because BINDABLE_AGENT_KEY_FILE is not set";
  return `${mode === "connected" ? "every Proposal" : "every local Track line"} is signed with agent key ${signer.keyId}`;
}

/** The canonical bytes a Proposal's signature covers. */
export function proposalSigningBytes(fields: SignedProposalFields): string {
  return canonicalJson({
    persistent_agent_id: fields.persistentAgentId,
    action: fields.action,
    nonce: fields.nonce,
    signed_at: fields.signedAt,
    parameters: fields.parameters,
    target: fields.target,
  });
}

/** Sign a Proposal's fields. */
export function signProposal(signer: Signer, fields: SignedProposalFields): ProposalSignature {
  return {
    agent_signature: signer.sign(proposalSigningBytes(fields)),
    agent_key_id: signer.keyId,
    signed_at: fields.signedAt,
  };
}

/** Whether `signature` (base64 DER) is `publicKey`'s over `bytes`. */
export function verifySignature(publicKey: KeyObject, bytes: string, signature: string): boolean {
  try {
    return verify(
      "sha256",
      Buffer.from(bytes, "utf8"),
      { key: publicKey, dsaEncoding: "der" },
      Buffer.from(signature, "base64"),
    );
  } catch {
    return false;
  }
}
