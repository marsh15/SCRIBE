"use server";

import { db } from "@/lib/db-config";
import { files, documents } from "@/lib/db-schema";
import { eq, and, sql } from "drizzle-orm";
import { getUserId } from "@/lib/auth";

export async function getFileWithChunks(fileId: number) {
    const userId = await getUserId();

    // Lightweight query: get file metadata + check if fileData exists (without loading it)
    const fileRows = await db
        .select({
            id: files.id,
            name: files.name,
            type: files.type,
            size: files.size,
            extractedText: files.extractedText,
            status: files.status,
            processingError: files.processingError,
            createdAt: files.createdAt,
            hasFileData: sql<boolean>`file_data IS NOT NULL`.as("has_file_data"),
            hasStorageUrl: sql<boolean>`storage_url IS NOT NULL`.as("has_storage_url"),
        })
        .from(files)
        .where(and(eq(files.id, fileId), eq(files.userId, userId)))
        .limit(1);

    const file = fileRows[0];
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

    return {
        file: {
            id: file.id,
            name: file.name,
            type: file.type,
            size: file.size,
            status: file.status,
            processingError: file.processingError,
            createdAt: file.createdAt,
        },
        chunks,
        extractedText: file.extractedText,
        hasFileData: file.hasFileData,
        hasStorageUrl: file.hasStorageUrl,
    };
}
