"use client";

import { useCallback, useState } from "react";
import { put } from "@vercel/blob/client";
import { sourceMimeTypeForName } from "@/lib/ingestion/source-intake-policy";

type UploadStatus = "idle" | "reserving" | "uploading" | "queued" | "error";

interface SourceUploadState {
  status: UploadStatus;
  progress: number;
  sourceId: number | null;
  error: string | null;
}

const INITIAL_STATE: SourceUploadState = {
  status: "idle",
  progress: 0,
  sourceId: null,
  error: null,
};

function sourceMimeType(file: File) {
  return sourceMimeTypeForName(file.name) ?? (file.type || "application/octet-stream");
}

export function useSourceUpload() {
  const [state, setState] = useState<SourceUploadState>(INITIAL_STATE);

  const uploadSource = useCallback(async (file: File) => {
    setState({ status: "reserving", progress: 0, sourceId: null, error: null });

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
        status: "queued",
        progress: 100,
        sourceId: reservation.source.id,
        error: null,
      });
      return { success: true as const, sourceId: reservation.source.id };
    } catch (error) {
      const message = error instanceof Error ? error.message : "Source upload failed";
      setState((current) => ({ ...current, status: "error", error: message }));
      return { success: false as const, error: message };
    }
  }, []);

  const reset = useCallback(() => setState(INITIAL_STATE), []);

  return { state, uploadSource, reset };
}
