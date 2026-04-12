"use client";

import { useState, useCallback, useRef } from "react";
import type { ClientChunk } from "@/lib/chunking-client";

const BATCH_SIZE = 100;
const STORAGE_PREFIX = "scribe:ingest:";

export type PipelineStatus =
  | "idle"
  | "extracting"
  | "chunking"
  | "uploading"
  | "finalizing"
  | "done"
  | "error";

export interface PipelineState {
  status: PipelineStatus;
  progress: number; // 0–100
  currentBatch: number;
  totalBatches: number;
  totalChunks: number;
  error: string | null;
}

interface StoredProgress {
  fileId: number;
  lastBatchIndex: number;
  totalBatches: number;
  chunks: ClientChunk[];
  textBytes: number;
}

function getStoredProgress(fileId: number): StoredProgress | null {
  try {
    const raw = localStorage.getItem(`${STORAGE_PREFIX}${fileId}`);
    if (!raw) return null;
    return JSON.parse(raw) as StoredProgress;
  } catch {
    return null;
  }
}

function saveProgress(data: StoredProgress) {
  try {
    localStorage.setItem(`${STORAGE_PREFIX}${data.fileId}`, JSON.stringify(data));
  } catch {
    // localStorage full or unavailable — non-fatal
  }
}

function clearProgress(fileId: number) {
  try {
    localStorage.removeItem(`${STORAGE_PREFIX}${fileId}`);
  } catch {
    // non-fatal
  }
}

/**
 * Orchestrator hook for the browser-side batch ingestion pipeline.
 * Handles sequential batch uploads, progress tracking, and localStorage-based resumability.
 */
export function useIngestionPipeline() {
  const [state, setState] = useState<PipelineState>({
    status: "idle",
    progress: 0,
    currentBatch: 0,
    totalBatches: 0,
    totalChunks: 0,
    error: null,
  });

  const abortRef = useRef(false);

  /**
   * Upload all chunks in sequential batches.
   * @param fileId - The file record ID on the server (already created during upload/sign flow)
   * @param chunks - All chunks produced by chunkContentClient
   * @param textBytes - Total character count of extracted text
   * @param startFromBatch - Resume from this batch index (0-based). Default 0.
   */
  const runPipeline = useCallback(
    async (
      fileId: number,
      chunks: ClientChunk[],
      textBytes: number,
      startFromBatch = 0,
      extractedText?: string
    ): Promise<{ success: boolean; error: string | null }> => {
      abortRef.current = false;

      const totalBatches = Math.ceil(chunks.length / BATCH_SIZE);

      setState({
        status: "uploading",
        progress: startFromBatch > 0
          ? Math.round((startFromBatch / totalBatches) * 100)
          : 0,
        currentBatch: startFromBatch,
        totalBatches,
        totalChunks: chunks.length,
        error: null,
      });

      try {
        for (let batchIdx = startFromBatch; batchIdx < totalBatches; batchIdx++) {
          if (abortRef.current) {
            setState((prev) => ({
              ...prev,
              status: "error",
              error: "Upload cancelled.",
            }));
            return { success: false, error: "Upload cancelled." };
          }

          const start = batchIdx * BATCH_SIZE;
          const end = Math.min(start + BATCH_SIZE, chunks.length);
          const batchChunks = chunks.slice(start, end);

          // Retry up to 2 times per batch for transient failures (timeouts, 429s)
          let res: Response | null = null;
          let lastBatchError = "";
          for (let attempt = 0; attempt < 3; attempt++) {
            try {
              res = await fetch("/api/ingest/batch", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({
                  fileId,
                  batchIndex: batchIdx,
                  totalBatches,
                  chunks: batchChunks,
                }),
              });

              if (res.ok) break; // Success — exit retry loop

              const errJson = await res.json().catch(() => ({ error: `HTTP ${res!.status}` }));
              lastBatchError = errJson.error || `Batch ${batchIdx + 1} failed (status ${res.status})`;

              // Don't retry 4xx client errors (validation, auth, quota)
              if (res.status >= 400 && res.status < 500) {
                throw new Error(lastBatchError);
              }
            } catch (fetchErr) {
              if (fetchErr instanceof Error && fetchErr.message === lastBatchError) throw fetchErr;
              lastBatchError = fetchErr instanceof Error ? fetchErr.message : "Network error";
            }

            // Wait before retrying (3 seconds)
            if (attempt < 2) {
              console.log(`[Pipeline] Batch ${batchIdx + 1} attempt ${attempt + 1} failed, retrying in 3s...`);
              await new Promise((r) => setTimeout(r, 3000));
            }
          }

          if (!res || !res.ok) {
            throw new Error(lastBatchError || `Batch ${batchIdx + 1} failed after 3 attempts`);
          }

          // Save progress to localStorage for resumability
          saveProgress({
            fileId,
            lastBatchIndex: batchIdx,
            totalBatches,
            chunks,
            textBytes,
          });

          const progress = Math.round(((batchIdx + 1) / totalBatches) * 100);
          setState({
            status: "uploading",
            progress,
            currentBatch: batchIdx + 1,
            totalBatches,
            totalChunks: chunks.length,
            error: null,
          });
        }

        // ---- Finalize ----
        setState((prev) => ({ ...prev, status: "finalizing" }));

        const finalizeRes = await fetch("/api/ingest/batch/finalize", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            fileId,
            totalChunks: chunks.length,
            textBytes,
            extractedText,
          }),
        });

        if (!finalizeRes.ok) {
          const errJson = await finalizeRes
            .json()
            .catch(() => ({ error: "Unknown error" }));
          throw new Error(errJson.error || "Finalization failed");
        }

        // ---- Done ----
        clearProgress(fileId);
        setState({
          status: "done",
          progress: 100,
          currentBatch: totalBatches,
          totalBatches,
          totalChunks: chunks.length,
          error: null,
        });
        return { success: true, error: null };
      } catch (err) {
        const message =
          err instanceof Error ? err.message : "Pipeline failed";
        setState((prev) => ({
          ...prev,
          status: "error",
          error: message,
        }));
        return { success: false, error: message };
      }
    },
    []
  );

  /**
   * Check localStorage for an incomplete upload and resume it.
   * Returns true if a resume was found and started, false otherwise.
   */
  const checkAndResume = useCallback(
    async (fileId: number): Promise<{ success: boolean; error: string | null }> => {
      const stored = getStoredProgress(fileId);
      if (!stored || stored.lastBatchIndex >= stored.totalBatches - 1) {
        clearProgress(fileId);
        return { success: false, error: null };
      }

      return runPipeline(
        stored.fileId,
        stored.chunks,
        stored.textBytes,
        stored.lastBatchIndex + 1
      );
    },
    [runPipeline]
  );

  /** Cancel the current upload (batch loop will stop after current batch completes). */
  const cancel = useCallback(() => {
    abortRef.current = true;
  }, []);

  /** Reset state to idle. */
  const reset = useCallback(() => {
    setState({
      status: "idle",
      progress: 0,
      currentBatch: 0,
      totalBatches: 0,
      totalChunks: 0,
      error: null,
    });
  }, []);

  return {
    state,
    runPipeline,
    checkAndResume,
    cancel,
    reset,
  };
}
