export interface TraceLinkCandidates {
  ragTraceId: string | null;
  retrievalTraceId: string | null;
}

type JsonObject = Record<string, unknown>;

function isObject(value: unknown): value is JsonObject {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

const UUID_RE = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

/** Read the last search trace IDs from the persisted assistant parts, never from request data. */
export function latestTraceLinkCandidates(parts: unknown): TraceLinkCandidates {
  const latest: TraceLinkCandidates = { ragTraceId: null, retrievalTraceId: null };
  if (!Array.isArray(parts)) return latest;

  for (const partValue of parts) {
    if (!isObject(partValue)) continue;
    const type = typeof partValue.type === "string" ? partValue.type : "";
    const toolName = type === "tool-invocation"
      ? partValue.toolName
      : type.startsWith("tool-")
        ? type.slice("tool-".length)
        : null;
    if (toolName !== "searchKnowledgeBase") continue;

    const result = isObject(partValue.result)
      ? partValue.result
      : isObject(partValue.output)
        ? partValue.output
        : null;
    if (!result || typeof result.traceId !== "string") continue;

    if (result.engine === "python" || UUID_RE.test(result.traceId)) {
      if (UUID_RE.test(result.traceId)) latest.retrievalTraceId = result.traceId;
    } else {
      latest.ragTraceId = result.traceId;
    }
  }

  return latest;
}
