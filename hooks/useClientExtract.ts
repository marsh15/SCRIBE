"use client";

import { useState, useCallback } from "react";

export interface ExtractionResult {
  extractedText: string;
  numPages?: number;
}

/**
 * Client-side document text extraction hook.
 * Uses pdfjs-dist for PDFs and FileReader for text-based formats.
 * All processing happens in the browser — zero server resources used.
 *
 * extractText() returns { result, error } synchronously — does NOT rely
 * on React state for the error, avoiding the async state flush bug.
 */
export function useClientExtract() {
  const [isExtracting, setIsExtracting] = useState(false);

  const extractText = useCallback(
    async (
      file: File
    ): Promise<{ result: ExtractionResult | null; error: string | null }> => {
      setIsExtracting(true);

      try {
        const ext = file.name.split(".").pop()?.toLowerCase();
        const type = file.type;

        // ---- PDF: use pdfjs-dist ----
        if (type === "application/pdf" || ext === "pdf") {
          const pdfjsLib = await import("pdfjs-dist");

          // Use the worker file copied to public/ during build.
          // This is the most reliable approach for pdfjs-dist v5 + Next.js.
          pdfjsLib.GlobalWorkerOptions.workerSrc = "/pdf.worker.min.mjs";

          const arrayBuffer = await file.arrayBuffer();
          const loadingTask = pdfjsLib.getDocument({
            data: new Uint8Array(arrayBuffer),
          });

          const pdf = await loadingTask.promise;
          const numPages = pdf.numPages;
          const pages: string[] = [];

          for (let i = 1; i <= numPages; i++) {
            const page = await pdf.getPage(i);
            const textContent = await page.getTextContent();
            const pageText = textContent.items
              .filter((item: any) => typeof item.str === "string")
              .map((item: any) => item.str)
              .join(" ");
            pages.push(pageText);
          }

          const extractedText = pages.join("\n\n");

          if (!extractedText.trim()) {
            return {
              result: null,
              error:
                "No text found in this PDF. It appears to be a scanned/image-only document that requires OCR.",
            };
          }

          return { result: { extractedText, numPages }, error: null };
        }

        // ---- Text / Markdown ----
        if (
          type === "text/plain" ||
          type === "text/markdown" ||
          ext === "txt" ||
          ext === "md"
        ) {
          const extractedText = await file.text();
          if (!extractedText.trim()) {
            return { result: null, error: "File is empty." };
          }
          return { result: { extractedText }, error: null };
        }

        // ---- CSV ----
        if (type === "text/csv" || ext === "csv") {
          const raw = await file.text();
          const extractedText = raw
            .split("\n")
            .filter((line) => line.trim())
            .map((line) =>
              line
                .split(",")
                .map((cell) => cell.trim().replace(/^"|"$/g, ""))
                .join(" ")
            )
            .join("\n");

          if (!extractedText.trim()) {
            return { result: null, error: "CSV file is empty." };
          }
          return { result: { extractedText }, error: null };
        }

        // ---- DOCX: not supported client-side ----
        if (
          type ===
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document" ||
          ext === "docx"
        ) {
          return {
            result: null,
            error:
              "DOCX files over 5MB are not supported in browser mode. Please convert to PDF first.",
          };
        }

        return { result: null, error: `Unsupported file type: .${ext || type}` };
      } catch (err) {
        const message =
          err instanceof Error ? err.message : "Text extraction failed";
        console.error("[useClientExtract] Extraction failed:", err);
        return { result: null, error: message };
      } finally {
        setIsExtracting(false);
      }
    },
    []
  );

  return { extractText, isExtracting };
}
