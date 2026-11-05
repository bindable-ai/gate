/**
 * Offline mode: what the Gate does while Bindable is unreachable.
 *
 * The TypeScript twin of the Python Gate's `bindable_gate.contract.offline`:
 * the same settings, the same words, and the same files in the same format,
 * so the person's `bindable-gate offline accept` reaches either Gate.
 *
 * THE SETTING. The Account chooses one of three in Bindable. A Gate reads it
 * with its own credential (`GET /v1/account/offline-mode`) while Bindable is
 * reachable, and keeps the last one on this machine for when it is not:
 *
 * - `automatic` (switch automatically): the action runs, with
 *   {@link UNREACHABLE_MESSAGE} said once;
 * - `ask_first` (ask me first): the action is blocked with {@link ASK_MESSAGE}
 *   until the person runs `bindable-gate offline accept`; then it runs. The
 *   person has {@link ACCEPT_WINDOW_MS} (10 minutes) from the outage's start;
 *   after that, with nobody accepted, the agent is stopped for the rest of
 *   the outage ({@link STOPPED_MESSAGE}), as under `stop`;
 * - `stop` (stop the agent): the Gate's fail-closed answer, unchanged.
 *
 * With nothing kept, the setting is `ask_first`.
 *
 * UNREACHABLE means no answer, a timeout or a 5xx, and nothing else
 * ({@link isUnreachable}). A 4xx is an answer, and so is a block: neither ever
 * switches.
 *
 * WHILE OFFLINE, no review is attempted. Each action that runs becomes one
 * offline record, in the form `POST /v1/agents/{did}/offline-records` takes:
 * the tool, the SHA-256 of its arguments (never the arguments), the SHA-256
 * of its result, whether it failed, when it started and finished, when the
 * outage began, the setting that let it run and, under ask me first, when the
 * person accepted and who (`accepted_by`, `user@host`; never with switch
 * automatically). One Gate's records for one agent form one chain:
 * `previous_hash` is the record before's `chain_hash` (64 zeros for the
 * first), and `chain_hash` is SHA-256 over the RFC 8785 canonical JSON of the
 * record without `chain_hash` and `agent_signature`. With an agent key the
 * record also carries `agent_key_id`, `signed_at` and `agent_signature` over
 * the same bytes. No Receipt is created, imitated or returned for any of it.
 *
 * RECONNECT. Bindable is probed with backoff. When it answers, the Gate goes
 * back to review at once and uploads the records, one offline period to a
 * request; a record leaves the upload list only on a 2xx, and none is ever
 * deleted.
 *
 * FILES, for one agent on this machine, under `~/.bindable/offline/<agent>/`
 * (`BINDABLE_OFFLINE_DIR` moves the root): `state.json`, `setting.json`,
 * `accepts.jsonl`, `records-<gate>.jsonl` (one chain per Gate), and `lock`
 * while one process changes them.
 */

import { createHash, randomUUID, type KeyObject } from "node:crypto";
import {
  appendFileSync,
  closeSync,
  existsSync,
  fstatSync,
  mkdirSync,
  openSync,
  readFileSync,
  readdirSync,
  readSync,
  renameSync,
  rmSync,
  statSync,
  writeFileSync,
  writeSync,
} from "node:fs";
import { homedir, hostname, userInfo } from "node:os";
import { join } from "node:path";
import { canonicalJson, sha256Hex, toJson } from "./canonical.js";
import type { FetchLike } from "./client.js";
import { DecisionUnavailable } from "./errors.js";
import { verifySignature, type Signer } from "./sign.js";

export const AUTOMATIC = "automatic";
export const ASK_FIRST = "ask_first";
export const STOP = "stop";
export const OFFLINE_SETTINGS = [AUTOMATIC, ASK_FIRST, STOP] as const;
export type OfflineSetting = (typeof OFFLINE_SETTINGS)[number];

/** The setting when none has been read from the platform. */
export const DEFAULT_OFFLINE_SETTING: OfflineSetting = ASK_FIRST;

/** How each setting is named to a person. */
export const OFFLINE_SETTING_NAMES: Readonly<Record<OfflineSetting, string>> = {
  automatic: "switch automatically",
  ask_first: "ask me first",
  stop: "stop the agent",
};

export const UNREACHABLE_MESSAGE = "Bindable is unreachable. This agent's actions are not being reviewed.";
export const ASK_MESSAGE =
  "Bindable is unreachable. This agent's actions are not being reviewed. " +
  "To continue anyway run: bindable-gate offline accept";
/**
 * How long ask me first waits, from the outage's start, for a person to
 * accept. A call is never held open that long: the plugin's hook deadline
 * would end it, and OpenClaw would then run the tool. So each call inside the
 * window is blocked with {@link ASK_MESSAGE} at once.
 */
export const ACCEPT_WINDOW_MS = 10 * 60 * 1000;
/** Said for each call once {@link ACCEPT_WINDOW_MS} has passed with nobody accepted. */
export const STOPPED_MESSAGE =
  "Bindable is unreachable and nobody ran bindable-gate offline accept within 10 minutes, " +
  "so this action was stopped. This agent stays stopped until Bindable is reachable again.";

/**
 * Whether {@link ACCEPT_WINDOW_MS} from the outage's start has passed. An
 * outage with no readable start counts as closed: nothing then runs.
 */
export function acceptWindowClosed(offlineSince: string | null | undefined, at: Date): boolean {
  const began = offlineSince ? Date.parse(offlineSince) : Number.NaN;
  return !Number.isFinite(began) || at.getTime() >= began + ACCEPT_WINDOW_MS;
}

/** What every offline record is, wherever this package shows one. */
export const NOT_REVIEWED = "Not reviewed: ran while Bindable was unreachable";

/** `1 offline record`, `3 offline records`. */
export function counted(n: number): string {
  return n === 1 ? `${n} offline record` : `${n} offline records`;
}

/** What the person is told when Bindable is back and the records went up. */
export function reconnectedMessage(sent: number): string {
  return `Bindable is reachable and your agent's actions are being reviewed again. ${counted(sent)} sent to the Track.`;
}

/** What the person is told when Bindable is back and the upload failed. */
export function keptMessage(kept: number): string {
  return (
    "Bindable is reachable and your agent's actions are being reviewed again. " +
    `${counted(kept)} ${kept === 1 ? "is" : "are"} kept on this machine and will be sent to the Track later.`
  );
}

export const SETTING_PATH = "/v1/account/offline-mode";
export const RECORDS_PATH = "/v1/agents/{did}/offline-records";
export const PROBE_PATH = "/v1/service-status";
export const BACKOFF_SECONDS = [5, 15, 30, 60, 120, 300] as const;
/** How long a kept setting is used before it is read again, while online. */
export const SETTING_REFRESH_MS = 15 * 60_000;
export const UPLOAD_BATCH = 500;
/** The most characters Bindable takes in `accepted_by`. */
export const ACCEPTED_BY_LIMIT = 100;
export const OFFLINE_DIR_ENV = "BINDABLE_OFFLINE_DIR";
export const STATE_FILE = "state.json";
export const SETTING_FILE = "setting.json";
export const ACCEPTS_FILE = "accepts.jsonl";

/** The file one Gate's chain is kept in, inside the agent's folder. */
export function recordsFile(chain: string): string {
  return `records-${chain.replace(/[^A-Za-z0-9_.-]/g, "-") || "gate"}.jsonl`;
}

/** Which Gate a chain is: the runtime or form `gateVersion` names, or `library`. */
export function chainOf(gateVersion: string): string {
  return gateVersion.includes("/") ? (gateVersion.split("/")[0] as string) : "library";
}
const LOCK_FILE = "lock";
const LOCK_WAIT_MS = 2_000;
const LOCK_STALE_MS = 30_000;
const GENESIS = "0".repeat(64);
const HASH = /^[0-9a-f]{64}$/;

const isSetting = (v: unknown): v is OfflineSetting => OFFLINE_SETTINGS.includes(v as OfflineSetting);
const text = (v: unknown): string | null => (typeof v === "string" && v ? v : null);
const sleep = (ms: number): Promise<void> => new Promise((resolve) => setTimeout(resolve, ms));

/**
 * Whether a review that failed with `error` means Bindable is unreachable:
 * no answer, a timeout or a 5xx. A refusal (any 4xx), an answer that could
 * not be read, or any other error is not.
 */
export function isUnreachable(error: unknown, timedOut = false): boolean {
  if (timedOut) return true;
  if (error instanceof DecisionUnavailable) {
    if (error.status !== undefined) return error.status >= 500;
    return error.noAnswer;
  }
  return false;
}

/** Where every agent's offline files are kept on this machine. */
export function offlineRoot(env: Readonly<Record<string, string | undefined>> = process.env): string {
  const configured = env[OFFLINE_DIR_ENV]?.trim();
  return configured ? configured : join(homedir(), ".bindable", "offline");
}

/** One agent's folder: named by the SHA-256 of its DID. */
export function agentFolder(agentId: string, root?: string): string {
  return join(root ?? offlineRoot(), createHash("sha256").update(agentId, "utf8").digest("hex").slice(0, 32));
}

/** Who is accepting: the user on this machine, and the machine. */
export function who(): string {
  let user = "unknown";
  try {
    user = userInfo().username;
  } catch {
    // no user name to give
  }
  return `${user}@${hostname()}`;
}

/** What offline mode decided for one action. */
export interface OfflineDecision {
  /** Whether the action may run, unreviewed, as an offline record. */
  run: boolean;
  /** What the person is shown: the block's reason, or the line said on switching. */
  message: string;
  setting: OfflineSetting;
  offlineSince: string;
  acceptedAt: string | null;
  acceptedBy: string | null;
}

/** One offline record, as kept and as sent: the platform's form, member for member. */
export interface OfflineRecord {
  id: string;
  previous_hash: string;
  chain_hash: string;
  agent_id: string;
  tool: string;
  params_digest: string;
  result_digest: string | null;
  failed: boolean;
  duration_ms: number | null;
  started_at: string;
  finished_at: string;
  offline_since: string;
  allowed_by: "automatic" | "ask_first";
  asked_at: string | null;
  accepted_by?: string;
  agent_key_id?: string;
  signed_at?: string;
  agent_signature?: string;
}

type State = Record<string, unknown>;

export interface OfflineOptions {
  agentId: string;
  baseUrl: string;
  credential: string;
  gateVersion: string;
  /** Which Gate's chain this is; from `gateVersion` when not given. */
  chain?: string;
  signer?: Signer;
  root?: string;
  fetch?: FetchLike;
  now?: () => Date;
  notify?: (message: string) => void;
  probeTimeoutMs?: number;
  uploadTimeoutMs?: number;
  uploadBudgetMs?: number;
}

/** What one record is made from. */
export interface OfflineAction {
  tool: string;
  /** The call's arguments: only their digest is kept. */
  params: Record<string, unknown>;
  startedAt: string;
  /** What the tool returned; left out when unknown. */
  result?: unknown;
  failed?: boolean;
  durationMs?: number;
}

/**
 * Offline mode for one agent, in one Gate. Call {@link before} before every
 * review, {@link failed} when one failed, {@link reviewed} after one that was
 * answered, and {@link record} once an action offline mode let run has run.
 * None of them throws.
 */
export class OfflineMode {
  readonly agentId: string;
  readonly folder: string;
  readonly chain: string;
  readonly #options: OfflineOptions;
  readonly #fetch: FetchLike;
  readonly #now: () => Date;
  readonly #notify: (message: string) => void;
  #queue: Promise<unknown> = Promise.resolve();

  constructor(options: OfflineOptions) {
    this.agentId = options.agentId;
    this.folder = agentFolder(options.agentId, options.root);
    this.chain = options.chain ?? chainOf(options.gateVersion);
    this.#options = options;
    this.#fetch = options.fetch ?? ((input, init) => fetch(input, init));
    this.#now = options.now ?? (() => new Date());
    this.#notify = options.notify ?? ((message) => process.stderr.write(message + "\n"));
  }

  get recordsPath(): string {
    return join(this.folder, recordsFile(this.chain));
  }

  /** The kept setting, or the default when none is kept. */
  setting(): OfflineSetting {
    const kept = readJson(join(this.folder, SETTING_FILE));
    return isSetting(kept?.when_unreachable) ? kept.when_unreachable : DEFAULT_OFFLINE_SETTING;
  }

  /**
   * Read the Account's setting from Bindable and keep it. Whatever the answer,
   * the next read waits {@link SETTING_REFRESH_MS}. Never throws.
   */
  async refreshSetting(): Promise<void> {
    const kept = readJson(join(this.folder, SETTING_FILE)) ?? {};
    let value: unknown = kept.when_unreachable;
    try {
      const response = await this.#fetch(`${this.#base()}${SETTING_PATH}`, {
        method: "GET",
        headers: { Authorization: `Bearer ${this.#options.credential}` },
        signal: AbortSignal.timeout(this.#options.probeTimeoutMs ?? 3_000),
        redirect: "error",
      });
      if (response.status === 200) {
        const answered = ((await response.json()) as Record<string, unknown>).when_unreachable;
        if (isSetting(answered)) value = answered;
      }
    } catch {
      // the kept one stands
    }
    try {
      mkdirSync(this.folder, { recursive: true, mode: 0o700 });
      writeJson(join(this.folder, SETTING_FILE), { when_unreachable: isSetting(value) ? value : null, read_at: this.#now().toISOString() });
    } catch {
      // read again next time
    }
  }

  /** Before a review. `undefined`: review as usual. Otherwise this decides. */
  async before(): Promise<OfflineDecision | undefined> {
    if (!existsSync(join(this.folder, STATE_FILE))) return undefined;
    try {
      return await this.#locked(async () => {
        const state = this.#state();
        if (!state.offline) {
          if (this.#pending(state) > 0 && due(state.next_upload_at, this.#now())) {
            await this.#upload(state, false);
            this.#save(state);
          }
          return undefined;
        }
        if (due(state.next_probe_at, this.#now())) {
          if (await this.#probe()) {
            await this.#back(state);
            this.#save(state);
            return undefined;
          }
          this.#schedule(state, "probe");
        }
        const decision = this.#decide(state);
        this.#save(state);
        return decision;
      });
    } catch {
      return undefined;
    }
  }

  /** After a review failed. `undefined`: the Gate's own fail-closed answer stands. */
  async failed(error: unknown, timedOut = false): Promise<OfflineDecision | undefined> {
    if (!isUnreachable(error, timedOut)) return undefined;
    const setting = this.setting();
    if (setting === STOP) return undefined;
    try {
      return await this.#locked(async () => {
        const state = this.#state();
        if (!state.offline) {
          Object.assign(state, {
            offline: true,
            offline_since: this.#now().toISOString(),
            setting_used: setting,
            accepted_at: null,
            accepted_by: null,
            announced: false,
            probe_attempts: 0,
          });
          this.#schedule(state, "probe");
        }
        const decision = this.#decide(state);
        this.#save(state);
        return decision;
      });
    } catch {
      return undefined;
    }
  }

  /** After a review that was answered: end an outage, and read the setting again when the kept one is old. */
  async reviewed(): Promise<void> {
    const state = readJson(join(this.folder, STATE_FILE));
    if (state?.offline) {
      try {
        await this.#locked(async () => {
          const current = this.#state();
          if (current.offline) {
            await this.#back(current);
            this.#save(current);
          }
        });
      } catch {
        // ended next time
      }
    }
    const readAt = Date.parse(String(readJson(join(this.folder, SETTING_FILE))?.read_at ?? ""));
    if (!Number.isFinite(readAt) || this.#now().getTime() - readAt >= SETTING_REFRESH_MS) await this.refreshSetting();
  }

  /** Append one offline record for an action that ran. Never throws. */
  async record(decision: OfflineDecision, action: OfflineAction): Promise<OfflineRecord | undefined> {
    try {
      return await this.#locked(async () => {
        const state = this.#state();
        const entry = this.#append(state, decision, action);
        this.#save(state);
        return entry;
      });
    } catch (error) {
      this.#notify(`Bindable Gate: the offline record for ${action.tool} was not written (${(error as Error).name ?? "error"}).`);
      return undefined;
    }
  }

  #decide(state: State): OfflineDecision {
    const setting = isSetting(state.setting_used) ? state.setting_used : DEFAULT_OFFLINE_SETTING;
    const acceptedAt = text(state.accepted_at);
    const since = text(state.offline_since) ?? "";
    if (setting === AUTOMATIC || acceptedAt) {
      const message = state.announced ? "" : UNREACHABLE_MESSAGE;
      state.announced = true;
      return { run: true, message, setting, offlineSince: since, acceptedAt, acceptedBy: text(state.accepted_by) };
    }
    const message = acceptWindowClosed(since, this.#now()) ? STOPPED_MESSAGE : ASK_MESSAGE;
    return { run: false, message, setting, offlineSince: since, acceptedAt: null, acceptedBy: null };
  }

  async #back(state: State): Promise<void> {
    const since = text(state.offline_since);
    if (since) state.periods = { ...((state.periods as Record<string, string>) ?? {}), [since]: this.#now().toISOString() };
    Object.assign(state, {
      offline: false,
      announced: false,
      offline_since: null,
      setting_used: null,
      accepted_at: null,
      accepted_by: null,
      next_probe_at: null,
      probe_attempts: 0,
      upload_attempts: 0,
      next_upload_at: null,
    });
    await this.#upload(state, true);
  }

  #append(state: State, decision: OfflineDecision, action: OfflineAction): OfflineRecord {
    const records = readRecords(this.recordsPath);
    const last = records[records.length - 1];
    const finishedAt = this.#now().toISOString();
    // An action that finished after Bindable was back still belongs to the
    // outage it ran in; that outage ends when the action did.
    const periods = (state.periods as Record<string, string> | undefined) ?? {};
    const ended = periods[decision.offlineSince];
    if (ended !== undefined && Date.parse(ended) < this.#now().getTime()) {
      periods[decision.offlineSince] = finishedAt;
      state.periods = periods;
    }
    const signer = this.#options.signer;
    const body: Omit<OfflineRecord, "chain_hash" | "agent_signature"> = {
      id: randomUUID(),
      previous_hash: last ? last.chain_hash : GENESIS,
      agent_id: this.agentId,
      tool: action.tool || "unknown",
      params_digest: sha256Hex(canonicalJson(toJson(action.params) ?? {})),
      result_digest: "result" in action ? digest(action.result) : null,
      failed: action.failed === true,
      duration_ms: typeof action.durationMs === "number" && Number.isFinite(action.durationMs) ? Math.max(0, Math.round(action.durationMs)) : null,
      started_at: action.startedAt,
      finished_at: finishedAt,
      offline_since: decision.offlineSince,
      allowed_by: decision.acceptedAt === null ? AUTOMATIC : ASK_FIRST,
      asked_at: decision.acceptedAt,
      ...(decision.acceptedAt !== null && decision.acceptedBy ? { accepted_by: decision.acceptedBy.slice(0, ACCEPTED_BY_LIMIT) } : {}),
      ...(signer ? { agent_key_id: signer.keyId, signed_at: finishedAt } : {}),
    };
    const bytes = canonicalJson(body);
    const entry: OfflineRecord = {
      ...(JSON.parse(bytes) as typeof body),
      chain_hash: sha256Hex(bytes),
      ...(signer ? { agent_signature: signer.sign(bytes) } : {}),
    };
    mkdirSync(this.folder, { recursive: true, mode: 0o700 });
    appendFileSync(this.recordsPath, JSON.stringify(entry) + "\n", { encoding: "utf8", mode: 0o600 });
    return entry;
  }

  #pending(state: State): number {
    const sent = Number(((state.sent as Record<string, number> | undefined) ?? {})[this.chain] ?? 0);
    return Math.max(0, countLines(this.recordsPath) - sent);
  }

  async #upload(state: State, reconnecting: boolean): Promise<void> {
    const pending = this.#pending(state);
    if (pending === 0) {
      if (reconnecting) this.#notify(reconnectedMessage(0));
      return;
    }
    const sent = await this.#send(state);
    if (sent === pending) {
      state.upload_attempts = 0;
      state.next_upload_at = null;
      this.#notify(reconnectedMessage(sent));
      return;
    }
    this.#schedule(state, "upload");
    if (reconnecting) this.#notify(keptMessage(pending - sent));
  }

  /** Send what is pending, in chain order, one offline period to a request. A period still going on is not sent. */
  async #send(state: State): Promise<number> {
    const done = ((state.sent as Record<string, number> | undefined) ?? {});
    state.sent = done;
    const through = Number(done[this.chain] ?? 0);
    let pending = readRecords(this.recordsPath).slice(through);
    const periods = (state.periods as Record<string, string> | undefined) ?? {};
    const started = Date.now();
    let sent = 0;
    while (pending.length > 0 && Date.now() - started <= (this.#options.uploadBudgetMs ?? 8_000)) {
      const since = (pending[0] as OfflineRecord).offline_since;
      const ended = periods[since];
      if (ended === undefined) break;
      const batch: OfflineRecord[] = [];
      for (const record of pending) {
        if (record.offline_since !== since || batch.length === UPLOAD_BATCH) break;
        batch.push(record);
      }
      let status: number;
      try {
        const response = await this.#fetch(this.#url(RECORDS_PATH), {
          method: "POST",
          headers: { Authorization: `Bearer ${this.#options.credential}`, "Content-Type": "application/json" },
          body: JSON.stringify({ offline_period: { started_at: since, ended_at: ended }, records: batch }),
          signal: AbortSignal.timeout(this.#options.uploadTimeoutMs ?? 5_000),
          redirect: "error",
        });
        status = response.status;
      } catch {
        break;
      }
      if (status !== 200 && status !== 201) break;
      sent += batch.length;
      done[this.chain] = through + sent;
      pending = pending.slice(batch.length);
    }
    return sent;
  }

  async #probe(): Promise<boolean> {
    try {
      const response = await this.#fetch(`${this.#base()}${PROBE_PATH}`, {
        method: "GET",
        headers: {},
        signal: AbortSignal.timeout(this.#options.probeTimeoutMs ?? 3_000),
        redirect: "error",
      });
      return response.status < 500;
    } catch {
      return false;
    }
  }

  #schedule(state: State, what: "probe" | "upload"): void {
    const attempts = Number(state[`${what}_attempts`] ?? 0);
    const gap = BACKOFF_SECONDS[Math.min(attempts, BACKOFF_SECONDS.length - 1)] as number;
    state[`${what}_attempts`] = attempts + 1;
    state[`next_${what}_at`] = new Date(this.#now().getTime() + gap * 1000).toISOString();
  }

  #state(): State {
    const state = readJson(join(this.folder, STATE_FILE)) ?? {};
    state.agent_id = this.agentId;
    return state;
  }

  #save(state: State): void {
    mkdirSync(this.folder, { recursive: true, mode: 0o700 });
    writeJson(join(this.folder, STATE_FILE), state);
  }

  /** One change at a time: in this process in turn, and across processes by the lock file. */
  #locked<T>(work: () => Promise<T>): Promise<T> {
    const run = this.#queue.then(() => withFolderLock(this.folder, work));
    this.#queue = run.catch(() => undefined);
    return run;
  }

  #base(): string {
    return this.#options.baseUrl.replace(/\/+$/, "");
  }

  #url(path: string): string {
    return this.#base() + path.replace("{did}", encodeURIComponent(this.agentId).replace(/%3A/g, ":"));
  }
}

// ─── The person's command ─────────────────────────────────────────────

/** One agent's offline state on this machine, as a command shows it. */
export interface AgentOffline {
  agentId: string;
  offline: boolean;
  setting: OfflineSetting;
  offlineSince: string | null;
  acceptedAt: string | null;
  acceptedBy: string | null;
  records: number;
  pending: number;
  /** Blocked under ask me first, nobody accepted yet. */
  waiting: boolean;
  /** Waiting past {@link ACCEPT_WINDOW_MS}: stopped until Bindable is back. */
  stopped: boolean;
  chains: string[];
}

/** Every agent with offline files on this machine. */
export function offlineAgents(root?: string, now: Date = new Date()): AgentOffline[] {
  const base = root ?? offlineRoot();
  if (!existsSync(base)) return [];
  const found: AgentOffline[] = [];
  for (const name of readdirSync(base).sort()) {
    const state = readJson(join(base, name, STATE_FILE));
    if (!state || typeof state.agent_id !== "string") continue;
    const sent = (state.sent as Record<string, number> | undefined) ?? {};
    const chains = readdirSync(join(base, name)).filter((f) => f.startsWith("records-") && f.endsWith(".jsonl")).sort();
    const counts = chains.map((f) => [f.slice("records-".length, -".jsonl".length), countLines(join(base, name, f))] as const);
    const total = counts.reduce((sum, [, n]) => sum + n, 0);
    const pending = counts.reduce((sum, [chain, n]) => sum + Math.max(0, n - Number(sent[chain] ?? 0)), 0);
    const offline = Boolean(state.offline);
    const setting = isSetting(state.setting_used) ? state.setting_used : DEFAULT_OFFLINE_SETTING;
    const acceptedAt = text(state.accepted_at);
    found.push({
      agentId: state.agent_id,
      offline,
      setting,
      offlineSince: text(state.offline_since),
      acceptedAt,
      acceptedBy: text(state.accepted_by),
      records: total,
      pending,
      waiting: offline && setting === ASK_FIRST && acceptedAt === null,
      stopped: offline && setting === ASK_FIRST && acceptedAt === null && acceptWindowClosed(text(state.offline_since), now),
      chains,
    });
  }
  return found;
}

function stoppedText(agent: AgentOffline): string {
  return (
    `Agent ${agent.agentId} was stopped: nobody accepted within 10 minutes of Bindable becoming ` +
    `unreachable at ${agent.offlineSince}. It runs again, reviewed, when Bindable is reachable`
  );
}

/** An offline command could not do what was asked; one plain sentence. */
export class OfflineError extends Error {
  override readonly name = "OfflineError";
}

/**
 * Accept running unreviewed for one agent on this machine, until Bindable is
 * back. Records who accepted and when.
 *
 * @throws OfflineError when no agent is waiting, several are and none was
 *   named, or the agent waited past {@link ACCEPT_WINDOW_MS} and is stopped.
 */
export async function acceptOffline(
  agentId?: string,
  options: { root?: string; by?: string; now?: () => Date } = {},
): Promise<AgentOffline> {
  const now = (options.now ?? (() => new Date()))();
  const asked = offlineAgents(options.root, now).filter((a) => a.waiting && (agentId === undefined || a.agentId === agentId));
  const waiting = asked.filter((a) => !a.stopped);
  if (asked.length > 0 && waiting.length === 0) throw new OfflineError(asked.map(stoppedText).join("; ") + ".");
  if (waiting.length === 0) {
    const named = agentId ? `Agent ${agentId} is` : "No agent on this machine is";
    throw new OfflineError(`${named} waiting to continue while Bindable is unreachable; there is nothing to accept.`);
  }
  if (waiting.length > 1) {
    throw new OfflineError(`More than one agent is waiting; name one with --agent-id: ${waiting.map((a) => a.agentId).join(", ")}`);
  }
  const chosen = waiting[0] as AgentOffline;
  const folder = agentFolder(chosen.agentId, options.root);
  const at = now.toISOString();
  const by = options.by ?? who();
  await withFolderLock(folder, async () => {
    const state = readJson(join(folder, STATE_FILE)) ?? {};
    if (!state.offline) throw new OfflineError(`Bindable is reachable again for agent ${chosen.agentId}; there is nothing to accept.`);
    if (acceptWindowClosed(text(state.offline_since), now)) throw new OfflineError(stoppedText(chosen) + ".");
    state.accepted_at = at;
    state.accepted_by = by;
    writeJson(join(folder, STATE_FILE), state);
    appendFileSync(
      join(folder, ACCEPTS_FILE),
      JSON.stringify({ agent_id: chosen.agentId, offline_since: state.offline_since ?? null, accepted_at: at, accepted_by: by }) + "\n",
      { encoding: "utf8", mode: 0o600 },
    );
  });
  return { ...chosen, acceptedAt: at, acceptedBy: by, waiting: false, stopped: false };
}

// ─── Checking a chain ─────────────────────────────────────────────────

export interface RecordsCheck {
  ok: boolean;
  records: number;
  brokenAt?: number;
  reason?: string;
}

/** Recompute the whole chain, as Bindable does. With `publicKey`, every record must carry a signature that key made. */
export function verifyRecords(path: string, publicKey?: KeyObject): RecordsCheck {
  const lines = readFileSync(path, "utf8").split("\n").filter((l) => l !== "");
  let previous = GENESIS;
  for (let i = 0; i < lines.length; i += 1) {
    const at = i + 1;
    let entry: Record<string, unknown>;
    try {
      entry = JSON.parse(lines[i] as string) as Record<string, unknown>;
    } catch {
      return { ok: false, records: lines.length, brokenAt: at, reason: "not JSON" };
    }
    const { chain_hash, agent_signature, ...body } = entry;
    if (body.previous_hash !== previous) return { ok: false, records: lines.length, brokenAt: at, reason: "previous_hash does not match" };
    if ("receipt_id" in body || "receipt" in body || "verdict" in body) {
      return { ok: false, records: lines.length, brokenAt: at, reason: "not an offline record" };
    }
    const bytes = canonicalJson(body);
    if (sha256Hex(bytes) !== chain_hash) return { ok: false, records: lines.length, brokenAt: at, reason: "chain_hash does not match" };
    if (publicKey && (typeof agent_signature !== "string" || !verifySignature(publicKey, bytes, agent_signature))) {
      return { ok: false, records: lines.length, brokenAt: at, reason: "agent_signature does not verify" };
    }
    previous = chain_hash as string;
  }
  return { ok: true, records: lines.length };
}

// ─── Files ────────────────────────────────────────────────────────────

/** Hold `folder`'s lock: a file made exclusively, naming this process, as the Python Gate takes it. */
async function withFolderLock<T>(folder: string, work: () => Promise<T>): Promise<T> {
  mkdirSync(folder, { recursive: true, mode: 0o700 });
  const path = join(folder, LOCK_FILE);
  const deadline = Date.now() + LOCK_WAIT_MS;
  for (;;) {
    try {
      const fd = openSync(path, "wx", 0o600);
      writeSync(fd, String(process.pid));
      closeSync(fd);
      break;
    } catch (error) {
      if ((error as NodeJS.ErrnoException).code !== "EEXIST") throw error;
      if (stale(path)) {
        rmSync(path, { force: true });
        continue;
      }
      if (Date.now() > deadline) throw new OfflineError("the offline files are held by another process");
      await sleep(10);
    }
  }
  try {
    return await work();
  } finally {
    rmSync(path, { force: true });
  }
}

function stale(path: string): boolean {
  let age: number;
  let holder: string;
  try {
    age = Date.now() - statSync(path).mtimeMs;
    holder = readFileSync(path, "utf8").trim();
  } catch {
    return false;
  }
  if (age > LOCK_STALE_MS) return true;
  if (!/^\d+$/.test(holder)) return age > 1_000;
  try {
    process.kill(Number(holder), 0);
    return false;
  } catch (error) {
    return (error as NodeJS.ErrnoException).code === "ESRCH";
  }
}

function readJson(path: string): Record<string, unknown> | undefined {
  try {
    const value = JSON.parse(readFileSync(path, "utf8")) as unknown;
    return value && typeof value === "object" && !Array.isArray(value) ? (value as Record<string, unknown>) : undefined;
  } catch {
    return undefined;
  }
}

function writeJson(path: string, value: unknown): void {
  const temporary = join(path, "..", `.${path.split("/").pop()}.${process.pid}.tmp`);
  writeFileSync(temporary, JSON.stringify(value), { encoding: "utf8", mode: 0o600 });
  renameSync(temporary, path);
}

function readRecords(path: string): OfflineRecord[] {
  if (!existsSync(path)) return [];
  return readFileSync(path, "utf8")
    .split("\n")
    .filter((l) => l !== "")
    .map((l) => JSON.parse(l) as OfflineRecord);
}

function countLines(path: string): number {
  if (!existsSync(path)) return 0;
  return readFileSync(path, "utf8").split("\n").filter((l) => l.trim() !== "").length;
}

function digest(result: unknown): string | null {
  if (result === undefined) return null;
  try {
    return sha256Hex(canonicalJson(result));
  } catch {
    return null;
  }
}

function due(at: unknown, now: Date): boolean {
  if (typeof at !== "string" || !at) return true;
  const moment = Date.parse(at);
  return !Number.isFinite(moment) || moment <= now.getTime();
}
