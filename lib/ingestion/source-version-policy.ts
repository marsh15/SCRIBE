import {
  SourceIntakeError,
  sourceVersionUploadPrefix,
  type CompletedSourceBlob,
} from "@/lib/ingestion/source-intake-policy";

export interface ReservedVersion {
  id: string;
  sourceId: number;
  versionNumber: number;
  sizeBytes: number | null;
  mimeType: string | null;
  uploadState: string;
  storageKey: string | null;
  duplicateOf: string | null;
}

export type CompleteOutcome = "queued" | "duplicate" | "replayed" | "source_deleted" | "not_found";

export interface VersionCompletionAdapters {
  loadVersion(versionId: string): Promise<ReservedVersion | null>;
  hashBlob(url: string): Promise<string>;
  /** scribe_complete_source_version: one atomic transition (see drizzle/0015). */
  complete(input: { versionId: string; url: string; sha256: string; size: number }): Promise<{
    outcome: CompleteOutcome;
    versionNumber: number | null;
    duplicateOf: string | null;
  }>;
  deleteBlob(url: string): Promise<void>;
  recordStorageUsage(version: ReservedVersion): Promise<void>;
}

export interface VersionCompletion {
  queued: boolean;
  replayed: boolean;
  duplicate: boolean;
  orphanDeleted: boolean;
  shouldKick: boolean;
  versionNumber: number | null;
}

const NONE: VersionCompletion = {
  queued: false,
  replayed: false,
  duplicate: false,
  orphanDeleted: false,
  shouldKick: false,
  versionNumber: null,
};

async function discard(adapters: VersionCompletionAdapters, url: string) {
  try {
    await adapters.deleteBlob(url);
  } catch (error) {
    console.warn("[SourceVersion] could not delete an unused blob", error);
  }
}

export function validateCompletedVersionBlob(
  userId: string,
  version: ReservedVersion,
  blob: CompletedSourceBlob
) {
  const contentType = blob.contentType.split(";", 1)[0]?.trim();
  if (
    !blob.pathname.startsWith(sourceVersionUploadPrefix(userId, version.sourceId, version.versionNumber)) ||
    (version.sizeBytes !== null && blob.size !== version.sizeBytes) ||
    (version.mimeType !== null && contentType !== version.mimeType)
  ) {
    throw new SourceIntakeError(
      "Uploaded Blob does not match its version reservation",
      409,
      "blob_mismatch"
    );
  }
}

/**
 * Blob callback for a reserved version. Idempotent: a version that already left 'uploading' is a replay and costs
 * no download; concurrent duplicates are serialised by the database function, so exactly one of them queues the job.
 */
export async function completeReservedVersionUpload(
  input: { versionId: string; sourceId: number; userId: string; blob: CompletedSourceBlob },
  adapters: VersionCompletionAdapters
): Promise<VersionCompletion> {
  const version = await adapters.loadVersion(input.versionId);
  if (!version) {
    await discard(adapters, input.blob.url);
    return { ...NONE, orphanDeleted: true };
  }
  if (version.sourceId !== input.sourceId) {
    throw new SourceIntakeError("Version does not belong to this Source", 409, "invalid_completion");
  }
  validateCompletedVersionBlob(input.userId, version, input.blob);
  if (version.uploadState !== "uploading") {
    const sameStoredBlob = version.uploadState === "stored" && version.duplicateOf === null && version.storageKey === input.blob.url;
    if (sameStoredBlob) await adapters.recordStorageUsage(version);
    return { ...NONE, replayed: true, shouldKick: sameStoredBlob, versionNumber: version.versionNumber };
  }

  const sha256 = await adapters.hashBlob(input.blob.url);
  const result = await adapters.complete({
    versionId: version.id,
    url: input.blob.url,
    sha256,
    size: input.blob.size,
  });

  switch (result.outcome) {
    case "queued":
      await adapters.recordStorageUsage(version);
      return { ...NONE, queued: true, shouldKick: true, versionNumber: result.versionNumber };
    case "duplicate":
      // identical bytes already live under another version: keep the row, drop the redundant original
      await discard(adapters, input.blob.url);
      return { ...NONE, duplicate: true, versionNumber: result.versionNumber };
    case "replayed": {
      const current = await adapters.loadVersion(input.versionId);
      const sameStoredBlob = !!current && current.sourceId === input.sourceId && current.uploadState === "stored" &&
        current.duplicateOf === null && current.storageKey === input.blob.url;
      if (sameStoredBlob) await adapters.recordStorageUsage(current);
      return { ...NONE, replayed: true, shouldKick: sameStoredBlob, versionNumber: result.versionNumber };
    }
    default:
      await discard(adapters, input.blob.url);
      return { ...NONE, orphanDeleted: true };
  }
}
