/** Display state of one Source version (pure: shared by the server action and the UI). */

export type VersionState =
  | "uploading"
  | "upload_failed"
  | "duplicate"
  | "active"
  | "queued"
  | "building"
  | "retrying"
  | "failed"
  | "superseded"
  | "cancelled";

export interface SourceVersionRow {
  id: string;
  versionNumber: number;
  sizeBytes: number | null;
  createdAt: string;
  uploadState: "uploading" | "stored" | "failed";
  duplicateOf: string | null;
  duplicateOfNumber: number | null;
  isActive: boolean;
  isDesired: boolean;
  generationStatus: string | null;
  jobStatus: string | null;
  jobStage: string | null;
  jobAttempts: number;
  errorCode: string | null;
  lastError: string | null;
}

export interface SourceVersionView extends SourceVersionRow {
  state: VersionState;
  label: string;
}

const LABELS: Record<VersionState, string> = {
  uploading: "Uploading",
  upload_failed: "Upload failed",
  duplicate: "Duplicate",
  active: "Active",
  queued: "Queued",
  building: "Indexing",
  retrying: "Retrying",
  failed: "Failed",
  superseded: "Replaced",
  cancelled: "Cancelled",
};

/**
 * Where a version stands. Order matters: a duplicate never builds, the active version is whatever the Source points
 * at (the previous version stays "Active" while a newer one builds or fails), then the generation, then the job.
 */
export function deriveVersionState(row: SourceVersionRow): VersionState {
  if (row.uploadState === "uploading") return "uploading";
  if (row.uploadState === "failed") return "upload_failed";
  if (row.duplicateOf) return "duplicate";
  if (row.isActive || row.generationStatus === "ACTIVE") return "active";
  if (row.generationStatus === "SUPERSEDED") return "superseded";
  if (row.generationStatus === "CANCELLED") return "cancelled";
  if (row.generationStatus === "FAILED") return "failed";
  switch (row.jobStatus) {
    case "failed":
      return "failed";
    case "superseded":
    case "cancelled":
      return "cancelled";
    case "retrying":
      return "retrying";
    case "processing":
      return "building";
    case "queued":
      return "queued";
  }
  if (row.generationStatus === "BUILDING" || row.generationStatus === "READY") return "building";
  return "queued";
}

export function toVersionView(row: SourceVersionRow): SourceVersionView {
  const state = deriveVersionState(row);
  return { ...row, state, label: LABELS[state] };
}
