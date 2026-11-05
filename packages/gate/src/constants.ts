/**
 * The wire contract, as the Python Gate in this repository speaks it.
 *
 * Two endpoints are called and no others: `POST /v1/reviews` for the Proposal
 * and `POST /v1/receipts/{receipt_id}/report` for the enforcement outcome. Both
 * present the Gate credential as a bearer token. `GET /v1/receipts/{receipt_id}`
 * takes a session or a developer key, not a Gate credential, so a Gate never
 * calls it.
 */

export const REVIEWS_PATH = "/v1/reviews";
export const REPORT_PATH = "/v1/receipts/{receipt_id}/report";

/** The two contexts an Action Review is performed in, as the wire spells them. */
export const REVIEW_CONTEXTS = ["test", "live"] as const;
export type ReviewContext = (typeof REVIEW_CONTEXTS)[number];
export const DEFAULT_REVIEW_CONTEXT: ReviewContext = "test";

/** What this kind of Gate is. */
export const RUNTIME_PLUGIN_GATE_KIND = "runtime_plugin";

/**
 * What a report carries as `gate_kind`. One constant, changed here and
 * nowhere else: the platform's report takes {@link RUNTIME_PLUGIN_GATE_KIND}.
 */
export const REPORT_GATE_KIND: string = RUNTIME_PLUGIN_GATE_KIND;

/**
 * Whether a Proposal's `agent_id` on the wire is the agent's did:webvh DID.
 * One constant: since 2026-11-01 the platform refuses the bare Agent ID there
 * and takes only the DID. Off, the Gate would send the Agent ID inside the DID.
 */
export const SEND_AGENT_DID: boolean = true;

/** The block reason whenever no decision could be obtained. */
export const DECISION_UNAVAILABLE = "decision unavailable";

/**
 * The longest one decision may take, every attempt included. OpenClaw waits
 * 15 seconds on a `before_tool_call` handler and lets the tool run when the
 * wait ends, so the Gate answers first.
 */
export const DECISION_DEADLINE_MS = 12_000;

/** Attempts at one Proposal or one report, the first included. */
export const ATTEMPTS = 2;

/** The verdict that releases, and the only one. */
export const PASSED = "passed";
