"use server";

import { db } from "@/lib/db-config";
import { files, documents } from "@/lib/db-schema";
import { eq, and } from "drizzle-orm";
import { getUserId } from "@/lib/auth";

export async function getFileWithChunks(fileId: number) {
    const userId = await getUserId();

    const file = await db.query.files.findFirst({
        where: and(eq(files.id, fileId), eq(files.userId, userId)),
    });

    if (!file) return null;

    const chunks = await db
        .select({
            id: documents.id,
            content: documents.content,
            metadata: documents.metadata,
        })
        .from(documents)
        .where(eq(documents.fileId, fileId))
        .orderBy(documents.id);

    return { file, chunks };
}
