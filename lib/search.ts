// lib/search.ts
import { cosineDistance, desc, gt, sql, eq, and } from "drizzle-orm";
import { db } from "./db-config";
import { documents, files } from "./db-schema";
import { generateEmbedding } from "./embeddings";

export type SearchDocumentResult = {
    id: number;
    content: string;
    metadata: unknown;
    file: {
        id: number;
        name: string;
        type: string;
    };
    similarity: number;
};

export type SearchDocumentsResult = {
    query: string;
    documents: SearchDocumentResult[];
    timings: {
        embeddingMs: number;
        retrievalMs: number;
        totalMs: number;
    };
    topK: number;
    threshold: number;
};

function elapsedMs(start: number) {
    return Math.max(0, Math.round(Date.now() - start));
}

/**
 * Search for similar documents using Drizzle ORM with cosineDistance.
 * Scoped to the given user's documents only.
 * Returns up to `limit` results across ALL uploaded documents.
 */
export async function searchDocuments(
    query: string,
    userId: string,
    limit: number = 10,
    threshold: number = 0.3
) : Promise<SearchDocumentsResult> {
    const totalStart = Date.now();
    const embeddingStart = Date.now();
    const embedding = await generateEmbedding(query);
    const embeddingMs = elapsedMs(embeddingStart);

    const similarity = sql<number>`1 - (${cosineDistance(
        documents.embeddings,
        embedding
    )})`;

    const retrievalStart = Date.now();
    const similarDocuments = await db
        .select({
            id: documents.id,
            content: documents.content,
            metadata: documents.metadata,
            file: {
                id: files.id,
                name: files.name,
                type: files.type,
            },
            similarity,
        })
        .from(documents)
        .innerJoin(files, eq(documents.fileId, files.id))
        .where(and(gt(similarity, threshold), eq(files.userId, userId)))
        .orderBy(desc(similarity))
        .limit(limit);
    const retrievalMs = elapsedMs(retrievalStart);

    return {
        query,
        documents: similarDocuments,
        timings: {
            embeddingMs,
            retrievalMs,
            totalMs: elapsedMs(totalStart),
        },
        topK: limit,
        threshold,
    };
}
