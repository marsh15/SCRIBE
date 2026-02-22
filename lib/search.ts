// lib/search.ts
import { cosineDistance, desc, gt, sql, eq } from "drizzle-orm";
import { db } from "./db-config";
import { documents, files } from "./db-schema";
import { generateEmbedding } from "./embeddings";

/**
 * Search for similar documents using Drizzle ORM with cosineDistance
 */
export async function searchDocuments(
    query: string,
    limit: number = 5,
    threshold: number = 0.5
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
        .where(gt(similarity, threshold))
        .orderBy(desc(similarity))
        .limit(limit);

    return similarDocuments;
}