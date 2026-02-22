import { RecursiveCharacterTextSplitter } from "@langchain/textsplitters";

export const textsplitter = new RecursiveCharacterTextSplitter({
    chunkSize: 1000,
    chunkOverlap: 200,
    separators: ["\n\n", "\n", " ", ""],
});

export async function chunkContent(content: string) {
    const rawChunks = await textsplitter.splitText(content.trim());
    return rawChunks.map((chunk, index) => ({
        content: chunk,
        metadata: {
            chunkIndex: index,
            totalChunks: rawChunks.length
        }
    }));
}
