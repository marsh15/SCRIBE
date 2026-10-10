"use server";

import { db } from "@/lib/db-config";
import { files } from "@/lib/db-schema";
import { and, eq, desc } from "drizzle-orm";
import { revalidatePath } from "next/cache";
import { resolveWorkspace } from "@/lib/workspace";
import { deleteSource } from "@/lib/ingestion/source-intake";

export async function getFiles() {
    try {
        const { userId, workspaceId } = await resolveWorkspace();
        const allFiles = await db.query.files.findMany({
            where: and(eq(files.userId, userId), eq(files.workspaceId, workspaceId)),
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
        const { userId } = await resolveWorkspace();
        const deleted = await deleteSource(userId, fileId);
        if (!deleted) return { success: false, error: "Source not found" };
        revalidatePath("/upload");
        return { success: true };
    } catch (error) {
        console.error("Failed to delete file:", error);
        return { success: false, error: "Failed to delete file" };
    }
}
