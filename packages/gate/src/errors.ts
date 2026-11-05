/**
 * Errors the Gate raises. No message ever carries the Gate credential or the
 * agent key: each is written here from fixed text and names a setting, never
 * its value.
 */

/** The configuration cannot be used. The Gate blocks every call until it is fixed. */
export class GateConfigError extends Error {
  override readonly name = "GateConfigError";
}

/** No decision was obtained: unreachable, timed out, refused, or unreadable. */
export class DecisionUnavailable extends Error {
  override readonly name = "DecisionUnavailable";
  constructor(
    message: string,
    /** The HTTP status, where the platform answered. */
    readonly status?: number,
    /** The platform's refusal code, where it gave one. */
    readonly code?: string,
    /** True when no attempt got any answer at all: what offline mode counts as unreachable. */
    readonly noAnswer: boolean = false,
  ) {
    super(message);
  }
}

/** A report could not be delivered. Logged as a gap; it never blocks anything. */
export class ReportNotRecorded extends Error {
  override readonly name = "ReportNotRecorded";
}
