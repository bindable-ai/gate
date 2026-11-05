/**
 * The Bindable Gate as an OpenClaw plugin.
 *
 * `before_tool_call`, with no matcher, so every tool: built-in, plugin and
 * MCP. Each call is a Proposal; released returns nothing and the call runs;
 * blocked returns `{ block: true, blockReason }` with Bindable's reason; no
 * decision returns `{ block: true, blockReason: "decision unavailable" }`.
 * Where Bindable is unreachable, the agent's offline setting decides in the
 * core: the block's reason is then the person's line, or the call runs, not
 * reviewed, and becomes an offline record.
 *
 * `after_tool_call`, with no matcher, reports what became of a call that ran.
 *
 * OpenClaw lets a tool run when a `before_tool_call` handler throws or times
 * out, and drops every hook of a plugin whose registration throws. So nothing
 * here throws: a configuration that cannot be used registers a handler that
 * blocks every call and says why, and the Gate answers inside the hook's
 * timeout, which is pinned here rather than left to the host's default.
 *
 * No allow-list, no tool restriction, no change to egress: the plugin only
 * needs the Bindable API host to be reachable.
 */

import {
  DECISION_DEADLINE_MS,
  DECISION_UNAVAILABLE,
  Gate,
  GateConfigError,
  readConfig,
  redactingLogger,
  signingNotice,
  type FetchLike,
  type GateConfig,
  type ToolCall,
} from "@bindable-ai/gate";
import type { AfterToolCallEvent, BeforeToolCallEvent, BeforeToolCallResult, PluginApi, PluginEntry, ToolContext } from "./openclaw.js";

export const PLUGIN_ID = "bindable-gate";
export const PLUGIN_VERSION = "0.1.0";
export const GATE_VERSION = `gate-openclaw/${PLUGIN_VERSION}`;

/** What OpenClaw waits on `before_tool_call`; the Gate's own deadline is under it. */
export const HOOK_TIMEOUT_MS = 15_000;

export interface PluginOptions {
  env?: Readonly<Record<string, string | undefined>>;
  fetch?: FetchLike;
  localTrackPath?: string;
}

/** The Proposal's payload: the tool's parameters and what OpenClaw says about the call. */
export function proposalPayload(event: BeforeToolCallEvent, ctx: ToolContext): Record<string, unknown> {
  const payload: Record<string, unknown> = { params: event.params ?? {} };
  const toolKind = event.toolKind ?? ctx.toolKind;
  const runId = event.runId ?? ctx.runId;
  const toolCallId = event.toolCallId ?? ctx.toolCallId;
  if (toolKind !== undefined) payload.toolKind = toolKind;
  if (event.derivedPaths !== undefined) payload.derivedPaths = [...event.derivedPaths];
  if (runId !== undefined) payload.runId = runId;
  if (toolCallId !== undefined) payload.toolCallId = toolCallId;
  if (ctx.agentId !== undefined) payload.agentId = ctx.agentId;
  if (ctx.sessionKey !== undefined) payload.sessionKey = ctx.sessionKey;
  return payload;
}

function toolCall(event: BeforeToolCallEvent | AfterToolCallEvent, ctx: ToolContext, payload?: Record<string, unknown>): ToolCall {
  const callId = event.toolCallId ?? ctx.toolCallId;
  return {
    tool: event.toolName ?? ctx.toolName ?? "",
    params: event.params ?? {},
    ...(payload ? { payload } : {}),
    ...(callId ? { callId } : {}),
  };
}

let localNoticePrinted = false;

/** Register the Gate's hooks. Synchronous, and never throws. */
export function register(api: PluginApi, options: PluginOptions = {}): void {
  const env = options.env ?? process.env;
  const credential = env.BINDABLE_GATE_CREDENTIAL?.trim();
  const logger = redactingLogger(api.logger, credential ? [credential] : []);

  let config: GateConfig;
  let gate: Gate;
  try {
    config = readConfig(env);
    gate = new Gate(config, {
      gateVersion: GATE_VERSION,
      logger,
      deadlineMs: DECISION_DEADLINE_MS,
      ...(options.fetch ? { fetch: options.fetch } : {}),
      ...(options.localTrackPath ? { localTrackPath: options.localTrackPath } : {}),
    });
  } catch (error) {
    const why = error instanceof GateConfigError ? error.message : "the Gate could not start";
    logger.error(`Bindable Gate: configuration invalid, so every tool call is blocked: ${why}`);
    const reason = `Bindable Gate configuration invalid: ${why}`;
    api.on("before_tool_call", () => ({ block: true, blockReason: reason }), { timeoutMs: HOOK_TIMEOUT_MS });
    return;
  }

  if (config.mode === "standalone") {
    if (!localNoticePrinted) {
      localNoticePrinted = true;
      logger.warn(
        `Bindable Gate: this agent is recording locally to its local Track (${gate.localTrackPath}) and is not connected to Bindable. ` +
          `Tool calls are allowed and not reviewed. Set BINDABLE_GATE_CREDENTIAL to connect. Signing: ${signingNotice(config.signer, "standalone")}.`,
      );
    }
  } else {
    logger.info(
      `Bindable Gate: connected; every tool call is reviewed by Bindable in the ${config.reviewContext} context for agent ${config.agent.did}; ${signingNotice(config.signer, "connected")}.`,
    );
  }

  api.on(
    "before_tool_call",
    async (event, ctx): Promise<BeforeToolCallResult | void> => {
      try {
        const decision = await gate.before(toolCall(event, ctx, proposalPayload(event, ctx)));
        if (decision.outcome === "released") return undefined;
        return { block: true, blockReason: decision.reason };
      } catch {
        return { block: true, blockReason: DECISION_UNAVAILABLE };
      }
    },
    { timeoutMs: HOOK_TIMEOUT_MS },
  );

  api.on("after_tool_call", async (event, ctx) => {
    try {
      await gate.after(toolCall(event, ctx), {
        ...("result" in event ? { result: event.result } : {}),
        ...(event.error !== undefined ? { error: event.error } : {}),
        ...(event.durationMs !== undefined ? { durationMs: event.durationMs } : {}),
      });
    } catch {
      // after_tool_call cannot enforce; the Gate has already logged any gap.
    }
  });

  api.on("gateway_stop", () => gate.flush());
}

/** The plugin entry, with options for a test. */
export function createPlugin(options: PluginOptions = {}): PluginEntry {
  return {
    id: PLUGIN_ID,
    name: "Bindable Gate",
    description: "Every tool call, built-in and MCP, reviewed by Bindable before it runs. Fails closed.",
    register: (api) => register(api, options),
  };
}

/** For tests: print the standalone notice again. */
export function resetLocalNotice(): void {
  localNoticePrinted = false;
}
