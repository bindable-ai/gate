/**
 * The two calls a Gate makes, and nothing else.
 *
 * Every request presents the Gate credential as `Authorization: Bearer` and
 * sends `Content-Type: application/json`. A transport failure is retried with
 * the same body, because the platform answers the same Proposal with the
 * Receipt it already minted; a refusal is an answer and is not retried, except
 * a report's 503, which is a store that failed.
 */

import { ATTEMPTS, REPORT_PATH, REVIEWS_PATH } from "./constants.js";
import { DecisionUnavailable, ReportNotRecorded } from "./errors.js";

export type FetchLike = (input: string, init: RequestInit) => Promise<Response>;

/** The Proposal as `POST /v1/reviews` takes it. */
export interface ProposalBody {
  agent_id: string;
  action_type: string;
  payload: Record<string, unknown>;
  proposed_action_id: string;
  nonce: string;
  proposed_at: string;
  review_context: string;
  target?: string;
  agent_signature?: string;
  agent_key_id?: string;
  signed_at?: string;
}

/** The enforcement outcome as `POST /v1/receipts/{receipt_id}/report` takes it. */
export interface ReportBody {
  outcome: "released" | "blocked";
  gate_kind: string;
  gate_version: string;
  action_result_digest: string | null;
  proposed_at: string;
  enforced_at: string;
}

/** What the review answered with 200. */
export interface ReviewAnswer {
  verdict?: unknown;
  failed_rule?: unknown;
  reason_code?: unknown;
  result_code?: unknown;
  receipt?: unknown;
}

export interface ClientOptions {
  baseUrl: string;
  credential: string;
  fetch?: FetchLike;
  attempts?: number;
}

const text = (v: unknown): string | undefined => (typeof v === "string" && v ? v : undefined);

export class BindableClient {
  readonly #baseUrl: string;
  readonly #credential: string;
  readonly #fetch: FetchLike;
  readonly #attempts: number;

  constructor(options: ClientOptions) {
    this.#baseUrl = options.baseUrl.replace(/\/+$/, "");
    this.#credential = options.credential;
    this.#fetch = options.fetch ?? ((input, init) => fetch(input, init));
    this.#attempts = Math.max(1, options.attempts ?? ATTEMPTS);
  }

  /**
   * Submit a Proposal. Returns the answer when the platform answered 200.
   *
   * @throws DecisionUnavailable for no answer, a non-200, or a body that is not
   *   a JSON object. Its message names the status and the platform's code and
   *   never the credential.
   */
  async review(body: ProposalBody, signal: AbortSignal): Promise<ReviewAnswer> {
    let last = "";
    for (let attempt = 0; attempt < this.#attempts; attempt += 1) {
      let response: Response;
      try {
        response = await this.#post(REVIEWS_PATH, body, signal);
      } catch (error) {
        last = transportFailure(error, signal);
        if (signal.aborted) break;
        continue;
      }
      if (response.status === 200) {
        const answer = await readJson(response);
        if (!answer || typeof answer !== "object" || Array.isArray(answer)) {
          throw new DecisionUnavailable("the review could not be read", 200);
        }
        return answer as ReviewAnswer;
      }
      const code = await refusalCode(response);
      throw new DecisionUnavailable(
        `the review was refused: ${response.status}${code ? ` ${code}` : ""}`,
        response.status,
        code,
      );
    }
    throw new DecisionUnavailable(`the review could not be reached: ${last || "no attempt finished"}`, undefined, undefined, true);
  }

  /**
   * Report an enforcement outcome. 201 created it; 200 is an identical retry
   * answered with the Trackpoint already stored.
   *
   * @throws ReportNotRecorded when it could not be delivered.
   */
  async report(receiptId: string, body: ReportBody, signal: AbortSignal): Promise<unknown> {
    const path = REPORT_PATH.replace("{receipt_id}", encodeURIComponent(receiptId));
    let last = "";
    for (let attempt = 0; attempt < this.#attempts; attempt += 1) {
      let response: Response;
      try {
        response = await this.#post(path, body, signal);
      } catch (error) {
        last = transportFailure(error, signal);
        if (signal.aborted) break;
        continue;
      }
      if (response.status === 200 || response.status === 201) return readJson(response);
      const code = await refusalCode(response);
      if (response.status === 503 && attempt + 1 < this.#attempts) {
        last = `503${code ? ` ${code}` : ""}`;
        continue;
      }
      throw new ReportNotRecorded(
        `the report for Receipt ${receiptId} was refused: ${response.status}${code ? ` ${code}` : ""}`,
      );
    }
    throw new ReportNotRecorded(
      `the report for Receipt ${receiptId} could not be delivered: ${last || "no attempt finished"}`,
    );
  }

  #post(path: string, body: unknown, signal: AbortSignal): Promise<Response> {
    return this.#fetch(`${this.#baseUrl}${path}`, {
      method: "POST",
      headers: {
        Authorization: `Bearer ${this.#credential}`,
        "Content-Type": "application/json",
      },
      body: JSON.stringify(body),
      signal,
      redirect: "error",
    });
  }
}

/** A transport failure in a few words: its kind, never its text, which can echo a request. */
function transportFailure(error: unknown, signal: AbortSignal): string {
  if (signal.aborted) return "timed out";
  if (error instanceof Error) return error.name || "network error";
  return "network error";
}

async function readJson(response: Response): Promise<unknown> {
  try {
    return await response.json();
  } catch {
    return undefined;
  }
}

async function refusalCode(response: Response): Promise<string | undefined> {
  const body = await readJson(response);
  if (!body || typeof body !== "object") return undefined;
  const code = text((body as Record<string, unknown>).code);
  // A refusal code is a short machine word; anything else is not repeated.
  return code && /^[a-z0-9_.-]{1,80}$/i.test(code) ? code : undefined;
}
