// lib/search.ts
import { cosineDistance, desc, gt, sql, eq, and } from "drizzle-orm";
import { db } from "./db-config";
import { documents, files } from "./db-schema";
import { generateEmbedding } from "./embeddings";

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
) {
    const embedding = await generateEmbedding(query);

    const similarity = sql<number>`1 - (${cosineDistance(
        documents.embeddings,
        embedding
    )})`;

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

    return similarDocuments;
}