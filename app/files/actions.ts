"use server";

import { db } from "@/lib/db-config";
import { files, documents } from "@/lib/db-schema";
import { eq, desc } from "drizzle-orm";
import { revalidatePath } from "next/cache";

export async function getFiles() {
    try {
        const allFiles = await db.query.files.findMany({
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
        // Due to the cascade constraint, deleting the file will also delete all associated document vectors
        await db.delete(files).where(eq(files.id, fileId));
        revalidatePath("/upload"); // Or wherever the list is shown
        return { success: true };
    } catch (error) {
        console.error("Failed to delete file:", error);
        return { success: false, error: "Failed to delete file" };
    }
}
