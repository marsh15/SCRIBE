"use client";

import { Loader2, RotateCcw } from "lucide-react";
import { Button } from "@/components/ui/button";

export interface IngestStatusProps {
  status: string;
  stage?: string | null;
  attempts?: number;
  errorCode?: string | null;
  lastError?: string | null;
  retrying?: boolean;
  onRetry?: () => void;
}

const STAGE_LABEL: Record<string, string> = {
  PARSE: "Reading document",
  CHUNK: "Splitting into passages",
  EMBED: "Embedding passages",
  DONE: "Finished",
  FAILED: "Stopped",
  CANCELLED: "Cancelled",
  SUPERSEDED: "Replaced by a newer version",
};

/** Stage, attempts and last error code for one Source, plus Retry once indexing has failed. */
export function SourceIngestStatus({ status, stage, attempts = 0, errorCode, lastError, retrying, onRetry }: IngestStatusProps) {
  if (status === "ready" || status === "uploading") return null;
  const stageLabel = stage ? (STAGE_LABEL[stage] ?? stage) : null;
  return (
    <div className="mt-1 flex flex-wrap items-center gap-x-3 gap-y-1" data-testid="ingest-status">
      {stageLabel && (
        <span className="font-mono text-[10px] text-muted-foreground" data-testid="ingest-stage">
          Stage: {stageLabel}
        </span>
      )}
      {attempts > 0 && (
        <span className="font-mono text-[10px] text-muted-foreground" data-testid="ingest-attempts">
          Attempts: {attempts}
        </span>
      )}
      {errorCode && (status === "failed" || status === "retrying") && (
        <span className="font-mono text-[10px] text-destructive/80" data-testid="ingest-error-code" title={lastError ?? undefined}>
          {errorCode}
        </span>
      )}
      {status === "failed" && onRetry && (
        <Button
          variant="outline"
          size="sm"
          className="h-6 rounded-sm px-2 text-[10px] font-mono uppercase tracking-wider"
          onClick={onRetry}
          disabled={retrying}
        >
          {retrying ? <Loader2 className="mr-1 h-3 w-3 animate-spin" /> : <RotateCcw className="mr-1 h-3 w-3" />}
          Retry
        </Button>
      )}
    </div>
  );
}

/** T22: a legacy Source whose original file is gone keeps working on the old index; uploading it again upgrades it. */
export function ReuploadNote({ reuploadRequiredAt }: { reuploadRequiredAt?: string | Date | null }) {
  if (!reuploadRequiredAt) return null;
  return (
    <p
      className="mt-1 font-mono text-[10px] text-muted-foreground"
      data-testid="reupload-note"
      title="The original file is no longer available, so this Source still uses the older index. Upload it again to upgrade it."
    >
      Re-upload to upgrade
    </p>
  );
}
