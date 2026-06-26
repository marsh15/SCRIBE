export const SOURCE_MIME_TYPES = [
  "application/pdf",
  "text/plain",
  "text/csv",
  "text/markdown",
  "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
] as const;

const SOURCE_MIME_BY_EXTENSION: Record<string, (typeof SOURCE_MIME_TYPES)[number]> = {
  pdf: "application/pdf",
  txt: "text/plain",
  csv: "text/csv",
  md: "text/markdown",
  docx: "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
};

export interface SourceUploadMetadata {
  name: string;
  size: number;
  type: string;
}

export interface CompletedSourceBlob {
  url: string;
  pathname: string;
  size: number;
  contentType: string;
}

export interface ReservedSource {
  id: number;
  userId: string;
  name: string;
  size: number;
  type: string;
  status: string;
  storageUrl: string | null;
}

export interface SourceCompletionAdapters {
  loadSource(sourceId: number, userId: string): Promise<ReservedSource | null>;
  deleteBlob(url: string): Promise<void>;
  transitionToQueued(source: ReservedSource, blob: CompletedSourceBlob): Promise<boolean>;
  recordStorageUsage(source: ReservedSource): Promise<void>;
}

export interface SourceDeletionAdapters {
  loadSource(sourceId: number, userId: string): Promise<ReservedSource | null>;
  deleteBlob(urlOrPathname: string): Promise<void>;
  deleteRecord(sourceId: number, userId: string): Promise<void>;
}

export class SourceIntakeError extends Error {
  constructor(
    message: string,
    readonly status: number,
    readonly code: string
  ) {
    super(message);
    this.name = "SourceIntakeError";
  }
}

export function sourceMimeTypeForName(name: string) {
  const extension = name.split(".").pop()?.toLowerCase() ?? "";
  return SOURCE_MIME_BY_EXTENSION[extension] ?? null;
}

function safeFileName(name: string) {
  const normalized = name.trim().replace(/[^a-zA-Z0-9._-]+/g, "-");
  return (normalized || "source").slice(-120);
}

export function sourceUploadPath(userId: string, sourceId: number, name: string) {
  return `sources/${encodeURIComponent(userId)}/${sourceId}/${safeFileName(name)}`;
}

export function parseSourceUploadMetadata(input: unknown): SourceUploadMetadata {
  if (!input || typeof input !== "object") {
    throw new SourceIntakeError("Invalid upload metadata", 400, "invalid_metadata");
  }

  const { name, size, type } = input as Partial<SourceUploadMetadata>;
  if (typeof name !== "string" || name.trim().length === 0 || name.length > 255) {
    throw new SourceIntakeError("Invalid file name", 400, "invalid_name");
  }
  if (!Number.isInteger(size) || !size || size <= 0) {
    throw new SourceIntakeError("Invalid file size", 400, "invalid_size");
  }
  const expectedType = sourceMimeTypeForName(name);
  if (
    typeof type !== "string" ||
    !expectedType ||
    type !== expectedType ||
    !SOURCE_MIME_TYPES.includes(type as never)
  ) {
    throw new SourceIntakeError(
      "Unsupported file type. Allowed types: PDF, TXT, CSV, Markdown, DOCX.",
      415,
      "unsupported_type"
    );
  }

  return { name: name.trim(), size, type };
}

export function validateSourceUploadAllowance(
  metadata: SourceUploadMetadata,
  usage: { allowOverage: boolean; projectedOverageInr: number },
  limit: { maxBytes: number; maxMb: number; planCode: string }
) {
  if (!usage.allowOverage && usage.projectedOverageInr > 0) {
    throw new SourceIntakeError(
      "Free plan storage or usage limit reached. Upgrade to upload more Sources.",
      402,
      "usage_limit"
    );
  }
  if (metadata.size > limit.maxBytes) {
    throw new SourceIntakeError(
      `File exceeds plan limit (${limit.maxMb} MB for ${limit.planCode.toUpperCase()} plan).`,
      413,
      "file_too_large"
    );
  }
}

export function retryDelayMinutes(attempts: number) {
  return Math.min(16, 2 ** Math.max(1, attempts));
}

export function shouldRetrySource(attempts: number) {
  return attempts < 5;
}

export function validateCompletedSourceBlob(
  source: { id: number; userId: string; name: string; size: number; type: string },
  blob: CompletedSourceBlob
) {
  const expectedPath = sourceUploadPath(source.userId, source.id, source.name);
  const normalizedContentType = blob.contentType.split(";", 1)[0]?.trim();
  if (
    blob.pathname !== expectedPath ||
    blob.size !== source.size ||
    normalizedContentType !== source.type
  ) {
    throw new SourceIntakeError(
      "Uploaded Blob does not match its Source reservation",
      409,
      "blob_mismatch"
    );
  }
}

export async function completeReservedSourceUpload(
  input: {
    sourceId: number;
    userId: string;
    blob: CompletedSourceBlob;
  },
  adapters: SourceCompletionAdapters
) {
  const source = await adapters.loadSource(input.sourceId, input.userId);
  if (!source) {
    await adapters.deleteBlob(input.blob.url);
    return { queued: false, replayed: false, orphanDeleted: true };
  }

  validateCompletedSourceBlob(source, input.blob);

  if (source.status !== "uploading") {
    if (source.storageUrl === input.blob.url) {
      return { queued: false, replayed: true, orphanDeleted: false };
    }
    throw new SourceIntakeError(
      `Source is in '${source.status}' state, expected 'uploading'`,
      409,
      "invalid_state"
    );
  }

  const queued = await adapters.transitionToQueued(source, input.blob);
  if (!queued) {
    return { queued: false, replayed: true, orphanDeleted: false };
  }

  await adapters.recordStorageUsage(source);
  return { queued: true, replayed: false, orphanDeleted: false };
}

export async function deleteOwnedSource(
  userId: string,
  sourceId: number,
  adapters: SourceDeletionAdapters
) {
  const source = await adapters.loadSource(sourceId, userId);
  if (!source) return false;

  if (source.storageUrl) {
    await adapters.deleteBlob(source.storageUrl);
  }
  await adapters.deleteRecord(sourceId, userId);
  return true;
}
