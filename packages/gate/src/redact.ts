/**
 * Keeping the Gate credential out of everything the Gate writes or sends on.
 */

export const REDACTED = "[redacted]";

/** A logger in the shape OpenClaw and most hosts provide. */
export interface Logger {
  debug?: (message: string) => void;
  info: (message: string) => void;
  warn: (message: string) => void;
  error: (message: string) => void;
}

/** `text` with every occurrence of each secret replaced. */
export function redact(text: string, secrets: readonly string[]): string {
  let out = text;
  for (const secret of secrets) {
    if (secret) out = out.split(secret).join(REDACTED);
  }
  return out;
}

/** A logger that redacts every secret from every line before it is written. */
export function redactingLogger(logger: Logger, secrets: readonly string[]): Logger {
  const kept = secrets.filter((s) => s.length > 0);
  const wrap = (write: (message: string) => void) => (message: string) =>
    write(redact(String(message), kept));
  return {
    ...(logger.debug ? { debug: wrap(logger.debug.bind(logger)) } : {}),
    info: wrap(logger.info.bind(logger)),
    warn: wrap(logger.warn.bind(logger)),
    error: wrap(logger.error.bind(logger)),
  };
}

/**
 * A copy of a JSON value with every secret masked inside every string, keys
 * included. A tool's parameters can carry the credential, for example a shell
 * command that echoes the environment, and a Proposal must never send it.
 */
export function maskSecrets<T>(value: T, secrets: readonly string[]): T {
  const kept = secrets.filter((s) => s.length > 0);
  if (kept.length === 0) return value;
  const walk = (v: unknown): unknown => {
    if (typeof v === "string") return redact(v, kept);
    if (Array.isArray(v)) return v.map(walk);
    if (v && typeof v === "object") {
      const out: Record<string, unknown> = {};
      for (const [k, inner] of Object.entries(v)) out[redact(k, kept)] = walk(inner);
      return out;
    }
    return v;
  };
  return walk(value) as T;
}
