"use server";

import { db } from "@/lib/db-config";
import { files } from "@/lib/db-schema";
import { eq, desc, and } from "drizzle-orm";
import { revalidatePath } from "next/cache";
import { getUserId } from "@/lib/auth";

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
        // Only allow deleting own files
        await db.delete(files).where(and(eq(files.id, fileId), eq(files.userId, userId)));
        revalidatePath("/upload");
        return { success: true };
    } catch (error) {
        console.error("Failed to delete file:", error);
        return { success: false, error: "Failed to delete file" };
    }
}
