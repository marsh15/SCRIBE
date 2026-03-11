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

    return rawChunks.map((chunk, index) => {
        // Estimate position in original document
        const chunkStart = content.indexOf(chunk);
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
