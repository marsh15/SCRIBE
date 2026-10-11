"use client";

import { useCallback, useState } from "react";
import { put } from "@vercel/blob/client";
import { sourceMimeTypeForName } from "@/lib/ingestion/source-intake-policy";

type UploadStatus =
  | "idle"
  | "reserving"
  | "uploading"
  | "queued"
  | "processing"
  | "ready"
  | "error";

interface SourceUploadState {
  status: UploadStatus;
  progress: number;
  sourceId: number | null;
  error: string | null;
  intakeSummary: string | null;
}

const INITIAL_STATE: SourceUploadState = {
  status: "idle",
  progress: 0,
  sourceId: null,
  error: null,
  intakeSummary: null,
};

function sourceMimeType(file: File) {
  return sourceMimeTypeForName(file.name) ?? (file.type || "application/octet-stream");
}

export function useSourceUpload() {
  const [state, setState] = useState<SourceUploadState>(INITIAL_STATE);

  const processSourceNow = useCallback(async (sourceId: number) => {
    for (let attempt = 1; attempt <= 3; attempt += 1) {
      const response = await fetch("/api/sources/process-now", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ sourceId }),
      });
      const result = (await response.json()) as {
        ok?: boolean;
        error?: string;
        claimed?: number;
        ready?: number;
        retrying?: number;
        failed?: number;
        ticked?: boolean;
      };

      if (!response.ok) {
        throw new Error(result.error || "Could not start Source intake");
      }

      if ((result.claimed ?? 0) > 0 || result.ticked) {
        return result;
      }

      if (attempt < 3) {
        await new Promise((resolve) => setTimeout(resolve, attempt * 1200));
      }
    }

    return { ok: true, claimed: 0, ready: 0, retrying: 0, failed: 0 };
  }, []);

  const uploadSource = useCallback(async (file: File) => {
    setState({
      status: "reserving",
      progress: 0,
      sourceId: null,
      error: null,
      intakeSummary: null,
    });

    try {
      const type = sourceMimeType(file);
      const reservationResponse = await fetch("/api/sources/reserve", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ name: file.name, size: file.size, type }),
      });
      const reservation = (await reservationResponse.json()) as {
        ok?: boolean;
        error?: string;
        source?: { id: number };
        uploadPath?: string;
        clientToken?: string;
      };

      if (
        !reservationResponse.ok ||
        !reservation.source?.id ||
        !reservation.uploadPath ||
        !reservation.clientToken
      ) {
        throw new Error(reservation.error || "Could not reserve the Source upload");
      }

      setState({
        status: "uploading",
        progress: 0,
        sourceId: reservation.source.id,
        error: null,
        intakeSummary: null,
      });

      await put(reservation.uploadPath, file, {
        access: "private",
        token: reservation.clientToken,
        contentType: type,
        multipart: file.size > 5 * 1024 * 1024,
        onUploadProgress: ({ percentage }) => {
          setState((current) => ({
            ...current,
            status: "uploading",
            progress: Math.round(percentage),
          }));
        },
      });

      setState({
        status: "processing",
        progress: 100,
        sourceId: reservation.source.id,
        error: null,
        intakeSummary: "Upload complete. Starting indexing now.",
      });

      let intake: Awaited<ReturnType<typeof processSourceNow>>;
      try {
        intake = await processSourceNow(reservation.source.id);
      } catch {
        intake = { ok: true, claimed: 0, ready: 0, retrying: 0, failed: 0 };
      }
      const nextStatus =
        (intake.ready ?? 0) > 0
          ? "ready"
          : (intake.claimed ?? 0) > 0 || intake.ticked
            ? "processing"
            : "queued";

      setState({
        status: nextStatus,
        progress: 100,
        sourceId: reservation.source.id,
        error: null,
        intakeSummary:
          nextStatus === "ready"
            ? "Source indexed and ready to cite."
            : nextStatus === "processing"
              ? "Indexing started. This page will refresh while it finishes."
              : "Source is queued. Use Process now if it does not start shortly.",
      });
      return { success: true as const, sourceId: reservation.source.id, status: nextStatus };
    } catch (error) {
      const message = error instanceof Error ? error.message : "Source upload failed";
      setState((current) => ({ ...current, status: "error", error: message }));
      return { success: false as const, error: message };
    }
  }, [processSourceNow]);

  /**
   * "Upload new version": reserve version N+1 of an existing Source, upload it privately, then nudge the queue.
   * The current version stays searchable the whole time; the server decides duplicates once the bytes are stored.
   */
  const uploadNewVersion = useCallback(async (sourceId: number, file: File) => {
    setState({ status: "reserving", progress: 0, sourceId, error: null, intakeSummary: null });
    try {
      const type = sourceMimeType(file);
      const response = await fetch(`/api/sources/${sourceId}/versions`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ name: file.name, size: file.size, type }),
      });
      const reservation = (await response.json()) as {
        error?: string;
        version?: { id: string; versionNumber: number };
        uploadPath?: string;
        clientToken?: string;
      };
      if (!response.ok || !reservation.version || !reservation.uploadPath || !reservation.clientToken) {
        throw new Error(reservation.error || "Could not reserve the new version");
      }
      setState({ status: "uploading", progress: 0, sourceId, error: null, intakeSummary: null });
      await put(reservation.uploadPath, file, {
        access: "private",
        token: reservation.clientToken,
        contentType: type,
        multipart: file.size > 5 * 1024 * 1024,
        onUploadProgress: ({ percentage }) =>
          setState((current) => ({ ...current, status: "uploading", progress: Math.round(percentage) })),
      });
      try {
        await processSourceNow(sourceId);
      } catch {
        // the tick chain and the daily sweep still pick the job up
      }
      setState({
        status: "processing",
        progress: 100,
        sourceId,
        error: null,
        intakeSummary: "New version uploaded. The current version stays searchable until it is indexed.",
      });
      return { success: true as const, sourceId, versionNumber: reservation.version.versionNumber };
    } catch (error) {
      const message = error instanceof Error ? error.message : "Version upload failed";
      setState((current) => ({ ...current, status: "error", error: message }));
      return { success: false as const, error: message };
    }
  }, [processSourceNow]);

  const reset = useCallback(() => setState(INITIAL_STATE), []);

  return { state, uploadSource, uploadNewVersion, processSourceNow, reset };
}
