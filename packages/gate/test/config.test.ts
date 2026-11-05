import { generateKeyPairSync } from "node:crypto";
import { readFileSync, writeFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";
import { GateConfigError, readConfig } from "../src/index.js";
import { AGENT_DID, AGENT_DID_WEB, AGENT_SCID, AGENT_UUID, CREDENTIAL, KEY_ID, connectedEnv, p256KeyFile, tempDir } from "./helpers.js";

function failure(env: Record<string, string>): GateConfigError {
  try {
    readConfig(env);
  } catch (error) {
    expect(error).toBeInstanceOf(GateConfigError);
    return error as GateConfigError;
  }
  throw new Error("readConfig did not fail");
}

describe("readConfig", () => {
  it("connected with the agent's did:webvh DID; test by default", () => {
    const config = readConfig(connectedEnv());
    expect(config).toMatchObject({
      mode: "connected",
      agent: { did: AGENT_DID, agentId: AGENT_UUID },
      baseUrl: "https://api.bindable.example",
      reviewContext: "test",
    });
  });

  it("takes live", () => {
    expect(readConfig(connectedEnv({ BINDABLE_REVIEW_CONTEXT: "live" }))).toMatchObject({ reviewContext: "live" });
  });

  it("refuses a bare Agent ID with a plain message, connected or standalone", () => {
    const message =
      "BINDABLE_AGENT_ID is a bare Agent ID, which Bindable no longer takes: give the agent's DID, did:webvh:<SCID>:<host>:agent:<Agent ID>";
    expect(failure(connectedEnv({ BINDABLE_AGENT_ID: AGENT_UUID })).message).toBe(message);
    expect(failure({ BINDABLE_AGENT_ID: AGENT_UUID }).message).toBe(message);
  });

  it.each([
    ["a did:web name", AGENT_DID_WEB],
    ["a did:webvh DID with no SCID", `did:webvh::bindable.example:agent:${AGENT_UUID}`],
    ["a did:webvh DID with the SCID placeholder", `did:webvh:{SCID}:bindable.example:agent:${AGENT_UUID}`],
    ["a did:webvh DID whose SCID is not base58", `did:webvh:Qm0OIl:bindable.example:agent:${AGENT_UUID}`],
    ["a did:webvh DID with no Agent ID", `did:webvh:${AGENT_SCID}:bindable.example:agent:main`],
  ])("refuses %s, connected or standalone", (_name, raw) => {
    const message = "BINDABLE_AGENT_ID is not the agent's DID: expected did:webvh:<SCID>:<host>:agent:<Agent ID>";
    expect(failure(connectedEnv({ BINDABLE_AGENT_ID: raw })).message).toBe(message);
    expect(failure({ BINDABLE_AGENT_ID: raw }).message).toBe(message);
  });

  it("derives the wire's Agent ID in lower case, and keeps the DID as given", () => {
    const upper = AGENT_DID.replace(AGENT_UUID, AGENT_UUID.toUpperCase());
    const config = readConfig(connectedEnv({ BINDABLE_AGENT_ID: upper }));
    expect(config.agent).toEqual({ did: upper, agentId: AGENT_UUID });
  });

  it("is standalone with no Gate credential", () => {
    expect(readConfig({}).mode).toBe("standalone");
    expect(readConfig({ BINDABLE_GATE_CREDENTIAL: "  " }).mode).toBe("standalone");
  });

  it("names what is missing or wrong", () => {
    expect(failure(connectedEnv({ BINDABLE_AGENT_ID: "agent-7" })).message).toMatch(/BINDABLE_AGENT_ID/);
    expect(failure(connectedEnv({ BINDABLE_REVIEW_CONTEXT: "prod" })).message).toMatch(/test or live/);
    expect(failure(connectedEnv({ BINDABLE_API_BASE_URL: "http://api.bindable.example" })).message).toMatch(/https/);
    const { BINDABLE_API_BASE_URL: _, ...noBase } = connectedEnv();
    expect(failure(noBase).message).toMatch(/BINDABLE_API_BASE_URL is required/);
  });
});

describe("the agent key: a wrong key file fails at startup", () => {
  it("loads a P-256 PEM", () => {
    const key = p256KeyFile();
    const config = readConfig(connectedEnv({ BINDABLE_AGENT_KEY_FILE: key.path, BINDABLE_AGENT_KEY_ID: KEY_ID }));
    expect(config.signer?.keyId).toBe(KEY_ID);
  });

  it("a missing file", () => {
    expect(failure(connectedEnv({ BINDABLE_AGENT_KEY_FILE: join(tempDir(), "absent.pem"), BINDABLE_AGENT_KEY_ID: KEY_ID })).message).toBe(
      "BINDABLE_AGENT_KEY_FILE could not be read",
    );
  });

  it("a file that is not a key, without repeating its contents", () => {
    const path = join(tempDir(), "not-a-key.pem");
    // A PEM's frame around text that is no key; the frame is built so no file holds a key header.
    const frame = (end: string) => `-----${end} ${"PRIVATE"} KEY-----`;
    writeFileSync(path, `${frame("BEGIN")}\n${CREDENTIAL}\n${frame("END")}\n`);
    const error = failure(connectedEnv({ BINDABLE_AGENT_KEY_FILE: path, BINDABLE_AGENT_KEY_ID: KEY_ID }));
    expect(error.message).toBe("BINDABLE_AGENT_KEY_FILE is not a PEM private key");
    expect(error.message).not.toContain(CREDENTIAL);
  });

  it("a key on another curve", () => {
    const path = join(tempDir(), "p384.pem");
    const { privateKey } = generateKeyPairSync("ec", { namedCurve: "secp384r1" });
    writeFileSync(path, privateKey.export({ type: "pkcs8", format: "pem" }));
    expect(failure(connectedEnv({ BINDABLE_AGENT_KEY_FILE: path, BINDABLE_AGENT_KEY_ID: KEY_ID })).message).toBe(
      "BINDABLE_AGENT_KEY_FILE is not an ECDSA P-256 private key",
    );
  });

  it("an RSA key, and a public key", () => {
    const dir = tempDir();
    const rsa = generateKeyPairSync("rsa", { modulusLength: 2048 });
    writeFileSync(join(dir, "rsa.pem"), rsa.privateKey.export({ type: "pkcs8", format: "pem" }));
    expect(failure(connectedEnv({ BINDABLE_AGENT_KEY_FILE: join(dir, "rsa.pem"), BINDABLE_AGENT_KEY_ID: KEY_ID })).message).toMatch(/P-256/);
    const key = p256KeyFile(dir);
    writeFileSync(join(dir, "pub.pem"), key.publicKey.export({ type: "spki", format: "pem" }));
    expect(failure(connectedEnv({ BINDABLE_AGENT_KEY_FILE: join(dir, "pub.pem"), BINDABLE_AGENT_KEY_ID: KEY_ID })).message).toMatch(/BINDABLE_AGENT_KEY_FILE/);
  });

  it("a key with no key id: there is no default", () => {
    const key = p256KeyFile();
    expect(failure(connectedEnv({ BINDABLE_AGENT_KEY_FILE: key.path })).message).toBe(
      "BINDABLE_AGENT_KEY_ID is required with BINDABLE_AGENT_KEY_FILE: <did>#<key uuid>",
    );
  });

  it.each([
    "key-1",
    `${AGENT_DID}#key-1`,
    `${AGENT_DID}#not-a-uuid`,
    `${AGENT_UUID}#${AGENT_UUID}`,
    `${AGENT_DID_WEB}#3c5e9a2b-7d14-4f0e-8b6a-2e9d1c4f7a05`,
  ])(
    "a key id that is not <did:webvh DID>#<key uuid>: %s",
    (keyId) => {
      const key = p256KeyFile();
      expect(failure(connectedEnv({ BINDABLE_AGENT_KEY_FILE: key.path, BINDABLE_AGENT_KEY_ID: keyId })).message).toBe(
        "BINDABLE_AGENT_KEY_ID is not a key id: expected <did>#<key uuid>, with the agent's did:webvh DID",
      );
    },
  );

  it("a key id for another agent", () => {
    const key = p256KeyFile();
    const other = KEY_ID.replace("bindable.example", "elsewhere.example");
    expect(failure(connectedEnv({ BINDABLE_AGENT_KEY_FILE: key.path, BINDABLE_AGENT_KEY_ID: other })).message).toMatch(
      /another agent/,
    );
  });

  it("no error message ever carries the key", () => {
    const key = p256KeyFile();
    const pem = readFileSync(key.path, "utf8");
    const body = pem.split("\n")[1]!;
    writeFileSync(key.path, pem.replace("PRIVATE KEY-----\n", "PRIVATE KEY-----\nAAAA"));
    const error = failure(connectedEnv({ BINDABLE_AGENT_KEY_FILE: key.path, BINDABLE_AGENT_KEY_ID: KEY_ID }));
    expect(error.message).not.toContain(body);
  });
});
