"use server";

import { db } from "@/lib/db-config";
import { files } from "@/lib/db-schema";
import { eq, desc } from "drizzle-orm";
import { revalidatePath } from "next/cache";
import { getUserId } from "@/lib/auth";
import { deleteSource } from "@/lib/ingestion/source-intake";

export async function getFiles() {
    try {
        const userId = await getUserId();
        const allFiles = await db.query.files.findMany({
            where: eq(files.userId, userId),
            orderBy: [desc(files.createdAt)],
        });
        return allFiles;
    } catch (error) {
        console.error("Failed to fetch files:", error);
        return [];
    }
}

export async function deleteFile(fileId: number) {
    try {
        const userId = await getUserId();
        const deleted = await deleteSource(userId, fileId);
        if (!deleted) return { success: false, error: "Source not found" };
        revalidatePath("/upload");
        return { success: true };
    } catch (error) {
        console.error("Failed to delete file:", error);
        return { success: false, error: "Failed to delete file" };
    }
}
