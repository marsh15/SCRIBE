const RETRYABLE_DATABASE_MESSAGES = [
  "connection terminated unexpectedly",
  "connection ended unexpectedly",
  "socket hang up",
  "fetch failed",
  "econnreset",
  "etimedout",
  "websocket is not open",
];

function sleep(ms: number) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

export function isRetryableDatabaseError(error: unknown) {
  if (!error || typeof error !== "object") {
    return false;
  }

  const details = error as { message?: string; code?: string };
  const message = String(details.message ?? "").toLowerCase();
  const code = String(details.code ?? "").toUpperCase();

  if (["ECONNRESET", "ETIMEDOUT", "57P01"].includes(code)) {
    return true;
  }

  return RETRYABLE_DATABASE_MESSAGES.some((entry) => message.includes(entry));
}

export async function withDatabaseRetry<T>(
  label: string,
  operation: () => Promise<T>,
  attempts = 3,
) {
  let lastError: unknown;

  for (let attempt = 1; attempt <= attempts; attempt += 1) {
    try {
      return await operation();
    } catch (error) {
      lastError = error;

      if (!isRetryableDatabaseError(error) || attempt === attempts) {
        throw error;
      }

      console.warn(`${label} failed with a transient database error; retrying`, {
        attempt,
        attempts,
        message: error instanceof Error ? error.message : String(error),
      });

      await sleep(150 * attempt);
    }
  }

  throw lastError instanceof Error ? lastError : new Error(`${label} failed`);
}
