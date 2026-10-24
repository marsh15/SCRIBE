"use server";

import { db } from "@/lib/db-config";
import { files, ingestionJobs, indexGenerations } from "@/lib/db-schema";
import { and, eq, desc, inArray, isNull } from "drizzle-orm";
import { revalidatePath } from "next/cache";
import { after } from "next/server";
import { env } from "@/lib/env";
import { requeueFailedSource } from "@/lib/ingestion/python-queue";
import { fireTick } from "@/lib/ingestion/tick";
import { resolveWorkspace } from "@/lib/workspace";
import { deleteSource } from "@/lib/ingestion/source-intake";
import { MAX_PAGE_SIZE } from "@/lib/files/source-detail-paging";
import { listSourceVersions } from "@/lib/ingestion/source-versions";

export async function getFiles() {
    try {
        const { userId, workspaceId } = await resolveWorkspace();
        const allFiles = await db.query.files.findMany({
            where: and(eq(files.userId, userId), eq(files.workspaceId, workspaceId), isNull(files.deletedAt)),
            orderBy: [desc(files.createdAt)],
        });
        if (allFiles.length === 0) return allFiles;
        // T18: show stage / attempts / last error code next to each Source.
        const jobs = await db
            .select({
                fileId: ingestionJobs.fileId,
                sourceVersionId: ingestionJobs.sourceVersionId,
                stage: ingestionJobs.stage,
                attempts: ingestionJobs.attempts,
                errorCode: ingestionJobs.errorCode,
                lastError: ingestionJobs.lastError,
                embeddedChunks: indexGenerations.embeddedChunks,
                expectedChunks: indexGenerations.expectedChunks,
                updatedAt: ingestionJobs.updatedAt,
            })
            .from(ingestionJobs)
            .leftJoin(indexGenerations, eq(ingestionJobs.generationId, indexGenerations.id))
            .where(inArray(ingestionJobs.fileId, allFiles.map((f) => f.id)))
            .orderBy(desc(ingestionJobs.updatedAt));
        const latest = new Map<number, (typeof jobs)[number]>();
        for (const job of jobs) if (!latest.has(job.fileId)) latest.set(job.fileId, job);
        return allFiles.map((file) => {
            const job = latest.get(file.id);
            return {
                ...file,
                jobStage: job?.stage ?? null,
                jobAttempts: job?.attempts ?? 0,
                jobErrorCode: job?.errorCode ?? null,
                jobLastError: job?.lastError ?? null,
                // When a new version is desired, don't show counts from its predecessor's generation.
                jobEmbeddedChunks: job?.sourceVersionId === file.desiredVersionId ? job.embeddedChunks : null,
                jobExpectedChunks: job?.sourceVersionId === file.desiredVersionId ? job.expectedChunks : null,
            };
        });
    } catch (error) {
        console.error("Failed to fetch files:", error);
        return [];
    }
}

export async function deleteFile(fileId: number) {
    try {
        const { userId } = await resolveWorkspace();
        // T20: soft delete. The Source leaves search immediately; blobs and rows are purged later by the daily sweep.
        const deleted = await deleteSource(userId, fileId);
        if (!deleted) return { success: false, error: "Source not found" };
        revalidatePath("/upload");
        return { success: true };
    } catch (error) {
        console.error("Failed to delete file:", error);
        return { success: false, error: "Failed to delete file" };
    }
}

/** Retry button: requeue a failed Source with a fresh attempt budget and kick the tick chain. */
export async function retrySource(fileId: number) {
    try {
        const { workspaceId } = await resolveWorkspace();
        const requeued = await requeueFailedSource(workspaceId, fileId);
        if (!requeued) return { success: false, error: "Only failed Sources can be retried" };
        if (env.INGEST_ENGINE === "python") after(async () => void (await fireTick()));
        revalidatePath("/upload");
        return { success: true };
    } catch (error) {
        console.error("Failed to retry Source:", error);
        return { success: false, error: "Could not retry this Source" };
    }
}

/** Version history for one Source (states, duplicates). Empty for a Source the caller does not own. */
export async function getSourceVersions(sourceId: number) {
    try {
        const { userId, workspaceId } = await resolveWorkspace();
        if (!Number.isInteger(sourceId) || sourceId <= 0) return [];
        return (await listSourceVersions(workspaceId, userId, sourceId, { limit: MAX_PAGE_SIZE })).items;
    } catch (error) {
        console.error("Failed to fetch Source versions:", error);
        return [];
    }
}
