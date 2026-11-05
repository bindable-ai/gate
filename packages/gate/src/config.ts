/**
 * The Gate's configuration, from environment variables.
 *
 *   BINDABLE_AGENT_ID          the agent's DID, did:webvh:<SCID>:<host>:agent:<Agent ID>
 *   BINDABLE_GATE_CREDENTIAL   the Gate credential; absent means standalone mode
 *   BINDABLE_API_BASE_URL      where Bindable is
 *   BINDABLE_REVIEW_CONTEXT    test or live; test when absent
 *   BINDABLE_AGENT_KEY_FILE    optional PEM ECDSA P-256 private key
 *   BINDABLE_AGENT_KEY_ID      required with a key: "<did:webvh DID>#<key uuid>"
 *
 * The did:webvh DID is the only agent identifier the Gate takes or shows, and
 * the one the agent's signature covers as persistent_agent_id. A bare Agent ID
 * or a did:web name is refused. A Proposal's agent_id is the DID too (see
 * SEND_AGENT_DID).
 *
 * Nothing here ever puts a value of the credential or the key into a message.
 */

import { DEFAULT_REVIEW_CONTEXT, REVIEW_CONTEXTS, SEND_AGENT_DID, type ReviewContext } from "./constants.js";
import { GateConfigError } from "./errors.js";
import { loadSigner, type Signer } from "./sign.js";

const UUID_TEXT = "[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}";
// did:webvh:<SCID>:<host>:agent:<Agent ID>. The SCID is base58btc.
const DID_WEBVH = new RegExp(
  `^did:webvh:([1-9A-HJ-NP-Za-km-z]+):([^:#?/\\s]+(?::[^:#?/\\s]+)*):agent:(${UUID_TEXT})$`,
);
const KEY_ID = new RegExp(`^(did:webvh:[^#\\s]+)#(${UUID_TEXT})$`);

const AGENT_ID_FORM = "did:webvh:<SCID>:<host>:agent:<Agent ID>";
const BARE_AGENT_ID = new RegExp(`^${UUID_TEXT}$`);

/** Who the agent is. */
export interface AgentIdentity {
  /** The agent's DID, did:webvh: the identifier the Gate is given, shows and signs. */
  did: string;
  /** The Agent ID inside the DID. Only a Proposal sent with SEND_AGENT_DID off carries it. */
  agentId: string;
}

/** Connected: every call is reviewed by Bindable. */
export interface ConnectedConfig {
  mode: "connected";
  agent: AgentIdentity;
  credential: string;
  baseUrl: string;
  reviewContext: ReviewContext;
  signer?: Signer;
}

/** Standalone: no Gate credential, so every call is allowed and recorded locally. */
export interface StandaloneConfig {
  mode: "standalone";
  agent?: AgentIdentity;
  signer?: Signer;
}

export type GateConfig = ConnectedConfig | StandaloneConfig;

type Env = Readonly<Record<string, string | undefined>>;

const value = (env: Env, name: string): string | undefined => {
  const raw = env[name];
  if (raw === undefined) return undefined;
  const trimmed = raw.trim();
  return trimmed === "" ? undefined : trimmed;
};

/** The agent's identity from its did:webvh DID. A bare Agent ID or a did:web name is refused. */
export function parseAgentId(raw: string): AgentIdentity {
  const match = DID_WEBVH.exec(raw);
  if (!match && BARE_AGENT_ID.test(raw)) {
    throw new GateConfigError(
      `BINDABLE_AGENT_ID is a bare Agent ID, which Bindable no longer takes: give the agent's DID, ${AGENT_ID_FORM}`,
    );
  }
  if (!match) {
    throw new GateConfigError(`BINDABLE_AGENT_ID is not the agent's DID: expected ${AGENT_ID_FORM}`);
  }
  return { did: raw, agentId: (match[3] as string).toLowerCase() };
}

/** What a Proposal's `agent_id` carries: the DID, or the Agent ID inside it with the switch off. */
export function wireAgentId(agent: AgentIdentity, sendDid: boolean = SEND_AGENT_DID): string {
  return sendDid ? agent.did : agent.agentId;
}

function parseBaseUrl(raw: string): string {
  let url: URL;
  try {
    url = new URL(raw);
  } catch {
    throw new GateConfigError("BINDABLE_API_BASE_URL is not a URL");
  }
  const loopback = ["localhost", "127.0.0.1", "[::1]"].includes(url.hostname);
  if (url.protocol !== "https:" && !(url.protocol === "http:" && loopback)) {
    throw new GateConfigError("BINDABLE_API_BASE_URL must be https (http only for localhost)");
  }
  if (url.username || url.password || url.search || url.hash) {
    throw new GateConfigError("BINDABLE_API_BASE_URL must carry no credentials, query or fragment");
  }
  return url.toString().replace(/\/+$/, "");
}

function parseReviewContext(raw: string | undefined): ReviewContext {
  if (raw === undefined) return DEFAULT_REVIEW_CONTEXT;
  if ((REVIEW_CONTEXTS as readonly string[]).includes(raw)) return raw as ReviewContext;
  throw new GateConfigError("BINDABLE_REVIEW_CONTEXT must be test or live");
}

function parseSigner(env: Env, agent: AgentIdentity | undefined): Signer | undefined {
  const path = value(env, "BINDABLE_AGENT_KEY_FILE");
  if (path === undefined) return undefined;
  const keyId = value(env, "BINDABLE_AGENT_KEY_ID");
  if (keyId === undefined) {
    throw new GateConfigError("BINDABLE_AGENT_KEY_ID is required with BINDABLE_AGENT_KEY_FILE: <did>#<key uuid>");
  }
  const match = KEY_ID.exec(keyId);
  if (!match || !DID_WEBVH.test(match[1] as string)) {
    throw new GateConfigError("BINDABLE_AGENT_KEY_ID is not a key id: expected <did>#<key uuid>, with the agent's did:webvh DID");
  }
  if (agent && match[1] !== agent.did) {
    throw new GateConfigError("BINDABLE_AGENT_KEY_ID names another agent than BINDABLE_AGENT_ID");
  }
  return loadSigner(path, keyId);
}

/**
 * Read the configuration. With no Gate credential the Gate is standalone.
 *
 * @throws GateConfigError when anything set cannot be used. The host must then
 *   block every call: a Gate whose configuration is wrong decides nothing.
 */
export function readConfig(env: Env = process.env): GateConfig {
  const credential = value(env, "BINDABLE_GATE_CREDENTIAL");
  const rawAgent = value(env, "BINDABLE_AGENT_ID");
  const agent = rawAgent === undefined ? undefined : parseAgentId(rawAgent);

  if (credential === undefined) {
    const signer = parseSigner(env, agent);
    return { mode: "standalone", ...(agent ? { agent } : {}), ...(signer ? { signer } : {}) };
  }

  if (!agent) throw new GateConfigError("BINDABLE_AGENT_ID is required with BINDABLE_GATE_CREDENTIAL");
  const rawBase = value(env, "BINDABLE_API_BASE_URL");
  if (rawBase === undefined) {
    throw new GateConfigError("BINDABLE_API_BASE_URL is required with BINDABLE_GATE_CREDENTIAL");
  }
  const baseUrl = parseBaseUrl(rawBase);
  const reviewContext = parseReviewContext(value(env, "BINDABLE_REVIEW_CONTEXT"));
  const signer = parseSigner(env, agent);
  return {
    mode: "connected",
    agent,
    credential,
    baseUrl,
    reviewContext,
    ...(signer ? { signer } : {}),
  };
}
