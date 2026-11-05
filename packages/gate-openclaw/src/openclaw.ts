/**
 * The parts of OpenClaw's plugin API this plugin uses, as OpenClaw's plugin
 * SDK declares them (openclaw 2026.10.7). Declared here so the plugin carries
 * no dependency on the host package.
 */

export interface PluginLogger {
  debug?: (message: string) => void;
  info: (message: string) => void;
  warn: (message: string) => void;
  error: (message: string) => void;
}

export interface BeforeToolCallEvent {
  toolName: string;
  params: Record<string, unknown>;
  toolKind?: string;
  toolInputKind?: string;
  runId?: string;
  toolCallId?: string;
  derivedPaths?: readonly string[];
}

export interface AfterToolCallEvent {
  toolName: string;
  params: Record<string, unknown>;
  runId?: string;
  toolCallId?: string;
  result?: unknown;
  error?: string;
  durationMs?: number;
}

export interface ToolContext {
  agentId?: string;
  sessionKey?: string;
  sessionId?: string;
  runId?: string;
  toolName: string;
  toolKind?: string;
  toolCallId?: string;
}

export interface BeforeToolCallResult {
  block?: boolean;
  blockReason?: string;
}

export interface HookOptions {
  priority?: number;
  timeoutMs?: number;
}

export interface PluginApi {
  pluginConfig?: Record<string, unknown>;
  logger: PluginLogger;
  on(
    hookName: "before_tool_call",
    handler: (event: BeforeToolCallEvent, ctx: ToolContext) => Promise<BeforeToolCallResult | void> | BeforeToolCallResult | void,
    opts?: HookOptions,
  ): void;
  on(
    hookName: "after_tool_call",
    handler: (event: AfterToolCallEvent, ctx: ToolContext) => Promise<void> | void,
    opts?: HookOptions,
  ): void;
  on(hookName: "gateway_stop", handler: () => Promise<void> | void, opts?: HookOptions): void;
}

export interface PluginEntry {
  id: string;
  name: string;
  description: string;
  register(api: PluginApi): void;
}
