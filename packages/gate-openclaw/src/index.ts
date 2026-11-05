import { createPlugin } from "./plugin.js";

export { GATE_VERSION, HOOK_TIMEOUT_MS, PLUGIN_ID, PLUGIN_VERSION, createPlugin, proposalPayload, register } from "./plugin.js";

/** The plugin OpenClaw loads: configured from the Gateway's environment. */
export default createPlugin();
