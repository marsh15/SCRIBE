/**
 * Browser-safe chunking module.
 * Mirrors lib/chunking.ts but can be imported from "use client" components.
 * Uses @langchain/textsplitters which has no Node-only dependencies.
 */
import { RecursiveCharacterTextSplitter } from "@langchain/textsplitters";

const splitter = new RecursiveCharacterTextSplitter({
  chunkSize: 4000,
  chunkOverlap: 400,
  separators: ["\n\n", "\n", " ", ""],
});

export interface ClientChunk {
  content: string;
  metadata: {
    chunkIndex: number;
    totalChunks: number;
    fileName?: string;
    estimatedPage?: number;
    totalPages?: number;
    positionPercent: number;
    section: number;
    charOffset?: number;
    charLength: number;
  };
}

interface ChunkOptions {
  fileName?: string;
  numPages?: number;
}

export async function chunkContentClient(
  content: string,
  options: ChunkOptions = {}
): Promise<ClientChunk[]> {
  const trimmed = content.trim();
  const rawChunks = await splitter.splitText(trimmed);
  const totalLength = trimmed.length;

  let searchOffset = 0;

  return rawChunks.map((chunk, index) => {
    const chunkStart = trimmed.indexOf(chunk, searchOffset);
    if (chunkStart >= 0) searchOffset = chunkStart + chunk.length;

    const positionPercent =
      chunkStart >= 0 ? Math.round((chunkStart / totalLength) * 100) : 0;

    let estimatedPage: number | undefined;
    if (options.numPages && chunkStart >= 0) {
      estimatedPage = Math.max(
        1,
        Math.ceil((chunkStart / totalLength) * options.numPages)
      );
    }

    const textBefore =
      chunkStart >= 0 ? trimmed.substring(0, chunkStart) : "";
    const sectionNumber = (textBefore.match(/\n\n/g) || []).length + 1;

    return {
      content: chunk,
      metadata: {
        chunkIndex: index,
        totalChunks: rawChunks.length,
        fileName: options.fileName,
        estimatedPage,
        totalPages: options.numPages,
        positionPercent,
        section: sectionNumber,
        charOffset: chunkStart >= 0 ? chunkStart : undefined,
        charLength: chunk.length,
      },
    };
  });
}
