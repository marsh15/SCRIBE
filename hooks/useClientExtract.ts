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
 */
export function useClientExtract() {
  const [isExtracting, setIsExtracting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const extractText = useCallback(
    async (file: File): Promise<ExtractionResult | null> => {
      setIsExtracting(true);
      setError(null);

      try {
        const ext = file.name.split(".").pop()?.toLowerCase();
        const type = file.type;

        // ---- PDF: use pdfjs-dist ----
        if (type === "application/pdf" || ext === "pdf") {
          // Dynamic import to avoid SSR issues and keep bundle small
          const pdfjsLib = await import("pdfjs-dist");

          // Point the worker to the CDN-hosted version matching our installed package
          pdfjsLib.GlobalWorkerOptions.workerSrc = `https://cdnjs.cloudflare.com/ajax/libs/pdf.js/${pdfjsLib.version}/pdf.worker.min.mjs`;

          const arrayBuffer = await file.arrayBuffer();
          const pdf = await pdfjsLib.getDocument({ data: arrayBuffer }).promise;
          const numPages = pdf.numPages;
          const pages: string[] = [];

          for (let i = 1; i <= numPages; i++) {
            const page = await pdf.getPage(i);
            const textContent = await page.getTextContent();
            const pageText = textContent.items
              .map((item: any) => item.str)
              .join(" ");
            pages.push(pageText);
          }

          const extractedText = pages.join("\n\n");

          if (!extractedText.trim()) {
            throw new Error(
              "No text found in PDF. It may be a scanned/image-only document."
            );
          }

          return { extractedText, numPages };
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
            throw new Error("File is empty.");
          }
          return { extractedText };
        }

        // ---- CSV ----
        if (type === "text/csv" || ext === "csv") {
          const raw = await file.text();
          // Simple CSV → text: join cells with spaces, rows with newlines
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
            throw new Error("CSV file is empty.");
          }
          return { extractedText };
        }

        // ---- DOCX: not supported client-side for now ----
        if (
          type ===
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document" ||
          ext === "docx"
        ) {
          throw new Error(
            "DOCX files over 5MB are not supported in browser mode. Please use a smaller file or convert to PDF."
          );
        }

        throw new Error(`Unsupported file type: .${ext || type}`);
      } catch (err) {
        const message =
          err instanceof Error ? err.message : "Text extraction failed";
        setError(message);
        return null;
      } finally {
        setIsExtracting(false);
      }
    },
    []
  );

  return { extractText, isExtracting, error };
}
