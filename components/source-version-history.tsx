"use client";

import { Loader2, Upload } from "lucide-react";
import { useRef } from "react";
import { Button } from "@/components/ui/button";
import type { SourceVersionView, VersionState } from "@/lib/ingestion/source-version-state";

const TONE: Record<VersionState, string> = {
  active: "bg-rag/15 text-rag",
  building: "bg-rag/15 text-rag",
  queued: "bg-muted text-muted-foreground",
  uploading: "bg-muted text-muted-foreground",
  retrying: "bg-accent/15 text-accent",
  failed: "bg-destructive/10 text-destructive",
  upload_failed: "bg-destructive/10 text-destructive",
  duplicate: "bg-muted text-muted-foreground",
  superseded: "bg-muted text-muted-foreground",
  cancelled: "bg-muted text-muted-foreground",
};

function formatSize(bytes: number | null) {
  if (bytes === null) return "";
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

function detail(version: SourceVersionView): string | null {
  switch (version.state) {
    case "duplicate":
      return version.duplicateOfNumber
        ? `Same file as v${version.duplicateOfNumber}; nothing was re-indexed.`
        : "Same file as an existing version; nothing was re-indexed.";
    case "failed":
      return version.isDesired || version.errorCode
        ? `Indexing failed${version.errorCode ? ` (${version.errorCode})` : ""}. The previous version is still searchable.`
        : "Indexing failed. The previous version is still searchable.";
    case "cancelled":
      return "Replaced by a newer upload before it finished.";
    case "superseded":
      return "Replaced by a newer version.";
    case "building":
    case "queued":
    case "retrying":
      return "The previous version stays searchable until this one is ready.";
    default:
      return null;
  }
}

export interface SourceVersionHistoryProps {
  versions: SourceVersionView[];
  loading?: boolean;
}

/** Version history of one Source: newest first, with each version's state and duplicates called out. */
export function SourceVersionHistory({ versions, loading }: SourceVersionHistoryProps) {
  if (loading && versions.length === 0) {
    return (
      <p className="mt-2 flex items-center gap-1.5 font-mono text-[10px] text-muted-foreground" data-testid="versions-loading">
        <Loader2 className="h-3 w-3 animate-spin" /> Loading versions
      </p>
    );
  }
  if (versions.length === 0) {
    return (
      <p className="mt-2 font-mono text-[10px] text-muted-foreground" data-testid="versions-empty">
        No version history yet.
      </p>
    );
  }
  return (
    <ol className="mt-2 space-y-1.5 border-l border-border/60 pl-3" data-testid="version-history">
      {versions.map((version) => {
        const note = detail(version);
        return (
          <li key={version.id} data-testid={`version-${version.versionNumber}`} data-state={version.state}>
            <div className="flex flex-wrap items-center gap-x-2 gap-y-0.5">
              <span className="font-mono text-[11px] text-foreground">v{version.versionNumber}</span>
              <span className={`rounded-sm px-1.5 py-0.5 font-mono text-[10px] uppercase ${TONE[version.state]}`} data-testid="version-state">
                {version.label}
              </span>
              <span className="font-mono text-[10px] text-muted-foreground">{formatSize(version.sizeBytes)}</span>
              <span className="font-mono text-[10px] text-muted-foreground">
                {new Date(version.createdAt).toLocaleDateString("en-US", { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" })}
              </span>
            </div>
            {note && <p className="text-[11px] text-muted-foreground">{note}</p>}
          </li>
        );
      })}
    </ol>
  );
}

export interface UploadNewVersionButtonProps {
  onFile: (file: File) => void;
  busy?: boolean;
  disabled?: boolean;
}

/** "Upload new version" action: a button wrapping a hidden file input. */
export function UploadNewVersionButton({ onFile, busy, disabled }: UploadNewVersionButtonProps) {
  const input = useRef<HTMLInputElement>(null);
  return (
    <>
      <input
        ref={input}
        type="file"
        accept=".pdf,.txt,.md,.csv,.docx"
        className="hidden"
        data-testid="new-version-input"
        onChange={(event) => {
          const file = event.target.files?.[0];
          event.target.value = "";
          if (file) onFile(file);
        }}
      />
      <Button
        variant="outline"
        size="sm"
        className="h-8 shrink-0 rounded-sm px-2 text-[10px] font-mono uppercase tracking-wider"
        onClick={() => input.current?.click()}
        disabled={busy || disabled}
      >
        {busy ? <Loader2 className="mr-1.5 h-3 w-3 animate-spin" /> : <Upload className="mr-1.5 h-3 w-3" />}
        Upload new version
      </Button>
    </>
  );
}
