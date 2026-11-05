/**
 * The Gate: one tool call, before and after.
 *
 * Connected, {@link Gate.before} submits the call as a Proposal and answers
 * released only when both the review's verdict and the Receipt's are exactly
 * `passed` and the Receipt carries a `receipt_id`. A blocked call is reported
 * `blocked` at once. {@link Gate.after} reports `released` for a call that
 * ran, or failed while running.
 *
 * FAIL-CLOSED. `before` never throws and answers within its own deadline,
 * set under the host's hook timeout. Refused, a non-2xx, an unreadable
 * answer, or an answer with no Receipt: each is blocked with "decision
 * unavailable".
 *
 * OFFLINE. Where Bindable is unreachable (no answer, a timeout or a 5xx), the
 * agent's offline setting decides (`offline.ts`): blocked with the person's
 * words, or released unreviewed, with no Receipt, and kept as an offline
 * record once the call has run. *Stop the agent* is the fail-closed answer
 * above, unchanged.
 *
 * Standalone (no Gate credential), every call is allowed and appended to the
 * local Track. Nothing is sent anywhere.
 */

import { randomBytes, randomUUID } from "node:crypto";
import { canonicalSha256, toJson } from "./canonical.js";
import { BindableClient, type FetchLike, type ProposalBody, type ReviewAnswer } from "./client.js";
import { wireAgentId, type ConnectedConfig, type GateConfig } from "./config.js";
import {
  DECISION_DEADLINE_MS,
  DECISION_UNAVAILABLE,
  PASSED,
  REPORT_GATE_KIND,
} from "./constants.js";
import { GateConfigError } from "./errors.js";
import { maskSecrets, redactingLogger, type Logger } from "./redact.js";
import { signProposal, type ProposalSignature } from "./sign.js";
import { OfflineMode, type OfflineDecision } from "./offline.js";
import { LocalTrack } from "./track.js";

/** One tool call, as the host adapter normalises it. */
export interface ToolCall {
  /** The tool's name: the Proposal's `action_type`. */
  tool: string;
  /** The tool's own parameters. */
  params: Record<string, unknown>;
  /** The Proposal's `payload`. The parameters alone when not given. */
  payload?: Record<string, unknown>;
  /** The host's id for this call, which ties `before` to `after`. */
  callId?: string;
}

/** What became of a call that ran. */
export interface ToolResult {
  result?: unknown;
  error?: string;
  durationMs?: number;
}

export type Decision =
  | { outcome: "released"; receiptId?: string; offline?: boolean }
  | { outcome: "blocked"; reason: string; receiptId?: string; unavailable: boolean };

export interface GateOptions {
  /** `gate_version` on every report, e.g. `gate-openclaw/0.1.0`. */
  gateVersion: string;
  logger?: Logger;
  fetch?: FetchLike;
  now?: () => Date;
  /** The longest `before` may take. Under the host's hook timeout. */
  deadlineMs?: number;
  /** Where the local Track is kept, standalone. */
  localTrackPath?: string;
  /** Where offline mode keeps each agent's files. `~/.bindable/offline` by default. */
  offlineDir?: string;
}

interface Pending {
  kind: "released" | "blocked" | "offline";
  tool: string;
  params: Record<string, unknown>;
  startedAt: string;
  receipt?: Record<string, unknown>;
  proposedAt?: string;
  payload?: Record<string, unknown>;
  offline?: OfflineDecision;
}

const TIMED_OUT = Symbol("timed out");

const MAX_PENDING = 10_000;
const silent: Logger = { info: () => {}, warn: () => {}, error: () => {} };
const str = (v: unknown): string | undefined => (typeof v === "string" && v ? v : undefined);
const isObject = (v: unknown): v is Record<string, unknown> =>
  typeof v === "object" && v !== null && !Array.isArray(v);

/** Whether this answer releases: `passed` on both, and a Receipt to tie it to. */
export function releases(answer: ReviewAnswer): boolean {
  const receipt = answer.receipt;
  return (
    answer.verdict === PASSED &&
    isObject(receipt) &&
    receipt.verdict === PASSED &&
    str(receipt.receipt_id) !== undefined
  );
}

/** The block reason for an answer that did not release, in Bindable's words. */
export function blockReason(tool: string, answer: ReviewAnswer): string {
  const receipt = isObject(answer.receipt) ? answer.receipt : {};
  const verdict = str(answer.verdict) ?? str(receipt.verdict) ?? "no verdict";
  const rule = str(answer.failed_rule) ?? str(receipt.failed_rule);
  let sentence = `Action Gate blocked ${tool}. Action Review returned ${verdict}`;
  sentence += rule ? ` under the rule ${rule}.` : ".";
  const detail = str(receipt.reason_detail);
  if (detail && detail.trim()) sentence += " " + detail.trim().split(/\s+/).join(" ");
  const id = str(receipt.receipt_id);
  return id ? `${sentence} Receipt ${id}.` : sentence;
}

export class Gate {
  readonly config: GateConfig;
  readonly mode: GateConfig["mode"];
  readonly #logger: Logger;
  readonly #now: () => Date;
  readonly #deadlineMs: number;
  readonly #gateVersion: string;
  readonly #client: BindableClient | undefined;
  readonly #track: LocalTrack | undefined;
  readonly #offline: OfflineMode | undefined;
  readonly #pending = new Map<string, Pending>();
  readonly #inflight = new Set<Promise<unknown>>();

  /**
   * @throws GateConfigError if the local Track cannot be opened, standalone.
   */
  constructor(config: GateConfig, options: GateOptions) {
    this.config = config;
    this.mode = config.mode;
    const secrets = config.mode === "connected" ? [config.credential] : [];
    this.#logger = redactingLogger(options.logger ?? silent, secrets);
    this.#now = options.now ?? (() => new Date());
    this.#deadlineMs = options.deadlineMs ?? DECISION_DEADLINE_MS;
    this.#gateVersion = options.gateVersion;
    if (config.mode === "connected") {
      this.#client = new BindableClient({
        baseUrl: config.baseUrl,
        credential: config.credential,
        ...(options.fetch ? { fetch: options.fetch } : {}),
      });
      this.#offline = new OfflineMode({
        agentId: config.agent.did,
        baseUrl: config.baseUrl,
        credential: config.credential,
        gateVersion: options.gateVersion,
        ...(config.signer ? { signer: config.signer } : {}),
        ...(options.offlineDir ? { root: options.offlineDir } : {}),
        ...(options.fetch ? { fetch: options.fetch } : {}),
        now: this.#now,
        notify: (message) => this.#logger.warn(message),
      });
    } else {
      this.#offline = undefined;
      const agentId = config.agent?.did;
      this.#track = new LocalTrack({
        ...(options.localTrackPath ? { path: options.localTrackPath } : {}),
        ...(agentId ? { agentId } : {}),
        ...(config.signer ? { signer: config.signer } : {}),
        now: this.#now,
      });
      try {
        this.#track.open();
      } catch (error) {
        if (error instanceof GateConfigError) throw error;
        throw new GateConfigError("the local Track could not be opened");
      }
    }
  }

  /** Where the local Track is kept, standalone. */
  get localTrackPath(): string | undefined {
    return this.#track?.path;
  }

  /** Decide one call. Never throws. */
  async before(call: ToolCall): Promise<Decision> {
    const startedAt = this.#now().toISOString();
    if (this.config.mode === "standalone") {
      this.#remember(call.callId, { kind: "released", tool: call.tool, params: call.params, startedAt });
      return { outcome: "released" };
    }
    const config = this.config;
    let timer: NodeJS.Timeout | undefined;
    try {
      const deadline = AbortSignal.timeout(this.#deadlineMs);
      const timedOut = new Promise<typeof TIMED_OUT>((resolve) => {
        timer = setTimeout(() => resolve(TIMED_OUT), this.#deadlineMs + 50);
      });
      const decided = await Promise.race([this.#decide(config, call, startedAt, deadline), timedOut]);
      if (decided === TIMED_OUT) return await this.#unavailable(config, call, undefined, true);
      return decided;
    } catch (error) {
      return await this.#unavailable(config, call, error, false);
    } finally {
      if (timer) clearTimeout(timer);
    }
  }

  /**
   * No decision could be obtained. Where Bindable is unreachable the agent's
   * offline setting decides; every other case, and *stop the agent*, blocks
   * with "decision unavailable".
   */
  async #unavailable(config: ConnectedConfig, call: ToolCall, error: unknown, timedOut: boolean): Promise<Decision> {
    const offline = await this.#offline?.failed(error, timedOut);
    if (offline) return this.#offlineAnswer(config, call, offline);
    this.#logger.warn(`Bindable Gate: ${call.tool || "a tool"} blocked, ${timedOut ? "the review timed out" : describe(error)}`);
    return unavailable();
  }

  /** Offline mode's answer. A call it lets run becomes an offline record once it has run. */
  #offlineAnswer(config: ConnectedConfig, call: ToolCall, offline: OfflineDecision): Decision {
    if (offline.message) this.#logger.warn(offline.message);
    if (!offline.run) return { outcome: "blocked", reason: offline.message, unavailable: true };
    const payload = maskSecrets(toJson(call.payload ?? call.params), [config.credential]);
    this.#remember(call.callId, {
      kind: "offline",
      tool: call.tool,
      params: call.params,
      // The action starts now, after the outage began: not when it was proposed.
      startedAt: this.#now().toISOString(),
      payload: isObject(payload) ? payload : {},
      offline,
    });
    return { outcome: "released", offline: true };
  }

  async #decide(config: ConnectedConfig, call: ToolCall, startedAt: string, signal: AbortSignal): Promise<Decision> {
    if (!call.tool) return { outcome: "blocked", reason: DECISION_UNAVAILABLE, unavailable: true };
    const payload = maskSecrets(toJson(call.payload ?? call.params), [config.credential]);
    if (!isObject(payload)) return unavailable();
    const offline = await this.#offline?.before();
    if (offline) return this.#offlineAnswer(config, call, offline);
    const proposedAt = startedAt;
    const body: ProposalBody = {
      agent_id: wireAgentId(config.agent),
      action_type: call.tool,
      payload,
      proposed_action_id: randomUUID(),
      nonce: randomBytes(16).toString("hex"),
      proposed_at: proposedAt,
      review_context: config.reviewContext,
    };
    // With a key, every Proposal is signed. A signature that cannot be made
    // throws here, and the call is blocked: nothing is sent unsigned instead.
    const signature = this.#sign(config, body);
    if (signature) Object.assign(body, signature);

    const answer = await (this.#client as BindableClient).review(body, signal);
    await this.#offline?.reviewed();
    const receipt = isObject(answer.receipt) ? answer.receipt : undefined;
    const receiptId = receipt ? str(receipt.receipt_id) : undefined;
    if (!receipt || !receiptId) {
      // No Receipt is no release, whatever else the answer said.
      this.#logger.warn(`Bindable Gate: ${call.tool} blocked, the review carried no Receipt`);
      return unavailable();
    }
    if (releases(answer)) {
      this.#remember(call.callId, { kind: "released", tool: call.tool, params: call.params, startedAt, receipt, proposedAt });
      return { outcome: "released", receiptId };
    }
    this.#remember(call.callId, { kind: "blocked", tool: call.tool, params: call.params, startedAt });
    this.#keep(this.#report(call.tool, receipt, proposedAt, "blocked", null));
    return { outcome: "blocked", reason: blockReason(call.tool, answer), receiptId, unavailable: false };
  }

  /** The signature for a Proposal, where an agent key is configured. */
  #sign(config: ConnectedConfig, body: ProposalBody): ProposalSignature | undefined {
    if (!config.signer) return undefined;
    return signProposal(config.signer, {
      persistentAgentId: config.agent.did,
      action: body.action_type,
      nonce: body.nonce,
      signedAt: this.#now().toISOString(),
      parameters: body.payload,
      // An OpenClaw tool call has no target; null is signed, so none can be added.
      target: null,
    });
  }

  /**
   * Record what became of a call that ran. Connected, report `released`;
   * standalone, append it to the local Track. Never throws.
   */
  async after(call: ToolCall, result: ToolResult): Promise<void> {
    try {
      const pending = call.callId ? this.#pending.get(call.callId) : undefined;
      if (call.callId) this.#pending.delete(call.callId);
      if (this.config.mode === "standalone") {
        (this.#track as LocalTrack).append({
          tool: call.tool,
          params: (toJson(pending?.params ?? call.params) as Record<string, unknown>) ?? {},
          startedAt: pending?.startedAt ?? this.#now().toISOString(),
          ...("result" in result ? { result: result.result } : {}),
          ...(result.error !== undefined ? { error: result.error } : {}),
          ...(result.durationMs !== undefined ? { durationMs: result.durationMs } : {}),
        });
        return;
      }
      if (pending?.kind === "blocked") return;
      if (pending?.kind === "offline") {
        // Ran unreviewed while Bindable was unreachable: an offline record, never a report.
        await this.#offline?.record(pending.offline as OfflineDecision, {
          tool: pending.tool,
          params: pending.payload ?? {},
          startedAt: pending.startedAt,
          ...(result.error === undefined && "result" in result ? { result: result.result } : {}),
          failed: result.error !== undefined,
          ...(result.durationMs !== undefined ? { durationMs: result.durationMs } : {}),
        });
        return;
      }
      if (!pending?.receipt || !pending.proposedAt) {
        this.#logger.warn(`Bindable Gate: report gap for ${call.tool || "a tool"}: no Receipt was kept for this call`);
        return;
      }
      let digest: string | null = null;
      if (result.error === undefined && result.result !== undefined) {
        try {
          digest = canonicalSha256(result.result);
        } catch {
          digest = null;
        }
      }
      const work = this.#report(call.tool, pending.receipt, pending.proposedAt, "released", digest);
      this.#keep(work);
      await work;
    } catch (error) {
      this.#logger.warn(`Bindable Gate: could not record ${call.tool || "a tool"}: ${describe(error)}`);
    }
  }

  /** Wait for every report still being delivered. */
  async flush(): Promise<void> {
    while (this.#inflight.size > 0) await Promise.allSettled([...this.#inflight]);
  }

  async #report(
    tool: string,
    receipt: Record<string, unknown>,
    proposedAt: string,
    outcome: "released" | "blocked",
    digest: string | null,
  ): Promise<void> {
    const receiptId = str(receipt.receipt_id) as string;
    try {
      await (this.#client as BindableClient).report(
        receiptId,
        {
          outcome,
          gate_kind: REPORT_GATE_KIND,
          gate_version: this.#gateVersion,
          action_result_digest: digest,
          proposed_at: proposedAt,
          enforced_at: this.#enforcedAt(receipt),
        },
        AbortSignal.timeout(this.#deadlineMs),
      );
    } catch (error) {
      this.#logger.warn(`Bindable Gate: report gap for ${tool}, Receipt ${receiptId}: ${describe(error)}`);
    }
  }

  /** Now, or the decision's time if the clock reads earlier. */
  #enforcedAt(receipt: Record<string, unknown>): string {
    const now = this.#now();
    const decided = str(receipt.decided_at);
    const decidedAt = decided ? Date.parse(decided) : NaN;
    return Number.isFinite(decidedAt) && decidedAt > now.getTime() ? (decided as string) : now.toISOString();
  }

  #remember(callId: string | undefined, pending: Pending): void {
    if (!callId) return;
    this.#pending.set(callId, pending);
    if (this.#pending.size > MAX_PENDING) {
      const oldest = this.#pending.keys().next().value as string;
      const dropped = this.#pending.get(oldest);
      this.#pending.delete(oldest);
      this.#logger.warn(`Bindable Gate: report gap for ${dropped?.tool ?? "a tool"}: its call never finished`);
    }
  }

  #keep(work: Promise<unknown>): void {
    this.#inflight.add(work);
    void work.finally(() => this.#inflight.delete(work));
  }
}

function unavailable(): Decision {
  return { outcome: "blocked", reason: DECISION_UNAVAILABLE, unavailable: true };
}

/** An error in a few words. Every message the Gate raises is its own fixed text. */
function describe(error: unknown): string {
  if (error instanceof Error) return `${error.name}: ${error.message}`;
  return "unknown error";
}
