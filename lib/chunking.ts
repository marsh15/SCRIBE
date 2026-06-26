import { RecursiveCharacterTextSplitter } from "@langchain/textsplitters";

export const textsplitter = new RecursiveCharacterTextSplitter({
    chunkSize: 4000,
    chunkOverlap: 400,
    separators: ["\n\n", "\n", " ", ""],
});

interface ChunkOptions {
    fileName?: string;
    numPages?: number;
}

export async function chunkContent(content: string, options: ChunkOptions = {}) {
    const rawChunks = await textsplitter.splitText(content.trim());
    const totalLength = content.trim().length;

    // Track cumulative search offset to prevent duplicate substrings from pointing
    // to an earlier position than they actually appear in the document.
    let searchOffset = 0;

    return rawChunks.map((chunk, index) => {
        const chunkStart = content.indexOf(chunk, searchOffset);
        // Chunks overlap, so the next chunk can begin before this chunk ends.
        // Advancing one character past the current start preserves overlap while
        // still preventing repeated text from resolving to the same occurrence.
        if (chunkStart >= 0) searchOffset = chunkStart + 1;

        const positionPercent = chunkStart >= 0 ? Math.round((chunkStart / totalLength) * 100) : 0;

        // Estimate page number if we know total pages
        let estimatedPage: number | undefined;
        if (options.numPages && chunkStart >= 0) {
            estimatedPage = Math.max(1, Math.ceil((chunkStart / totalLength) * options.numPages));
        }

        // Count which "section" of the document this is in (roughly by paragraph breaks)
        const textBefore = chunkStart >= 0 ? content.substring(0, chunkStart) : "";
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
            }
        };
    });
}
