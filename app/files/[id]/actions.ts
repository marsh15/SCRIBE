"use server";

import { db } from "@/lib/db-config";
import { files, documents } from "@/lib/db-schema";
import { eq, and, sql, isNull } from "drizzle-orm";
import { isNotAuthenticatedError } from "@/lib/auth";
import { resolveWorkspace } from "@/lib/workspace";
import { ensureDocumentsChunkIndexSchema } from "@/lib/db-compat";
import {
    getOcrSummary,
    getSourceHeader,
    listChunks,
    listIngestionHistory,
    listStructure,
    type ChunkKind,
    type ChunkView,
    type JobHistoryEntry,
    type OcrSummary,
    type SourceHeader,
} from "@/lib/files/source-detail";
import { isValidSourceId, type Page, type PageRequest, type StructureBlock } from "@/lib/files/source-detail-paging";
import { listSourceVersions } from "@/lib/ingestion/source-versions";
import type { SourceVersionView } from "@/lib/ingestion/source-version-state";

/** The legacy chunk list is bounded like every other list on this page. */
const MAX_LEGACY_CHUNKS = 200;

export async function getFileWithChunks(fileId: number) {
    try {
        const { userId, workspaceId } = await resolveWorkspace();

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
            .where(and(eq(files.id, fileId), eq(files.userId, userId), eq(files.workspaceId, workspaceId), isNull(files.deletedAt)))
            .limit(1);

        const file = fileRows[0];
        if (!file) return null;

        const filePayload = {
            id: file.id,
            name: file.name,
            type: file.type,
            size: file.size,
            status: file.status,
            processingError: file.processingError,
            createdAt: file.createdAt,
        };

        if (file.status !== "ready") {
            return {
                file: filePayload,
                chunks: [],
                extractedText: file.extractedText,
                hasFileData: file.hasFileData,
                hasStorageUrl: file.hasStorageUrl,
            };
        }

        await ensureDocumentsChunkIndexSchema();

        const chunks = await db
            .select({
                id: documents.id,
                chunkIndex: documents.chunkIndex,
                content: documents.content,
                metadata: documents.metadata,
            })
            .from(documents)
            .where(eq(documents.fileId, fileId))
            .orderBy(documents.chunkIndex)
            .limit(MAX_LEGACY_CHUNKS);

        return {
            file: filePayload,
            chunks,
            extractedText: file.extractedText,
            hasFileData: file.hasFileData,
            hasStorageUrl: file.hasStorageUrl,
        };
    } catch (error) {
        console.error("Failed to load file with chunks:", error);
        return {
            error: isNotAuthenticatedError(error)
                ? "Sign in again to view this document."
                : "Could not load this document. Check the server logs for the full error.",
        };
    }
}

// ------------------------------------------------------------------------------------------ T24 detail tabs

export type DetailResult<T> =
    | { ok: true; data: T }
    | { ok: false; status: 401 | 404 | 500; error: string };

async function detail<T>(
    sourceId: number,
    run: (ctx: { userId: string; workspaceId: string }) => Promise<T | null>
): Promise<DetailResult<T>> {
    if (!isValidSourceId(sourceId)) return { ok: false, status: 404, error: "Source not found" };
    try {
        const ctx = await resolveWorkspace();
        const data = await run(ctx);
        // a foreign, soft-deleted or missing Source is indistinguishable: 404
        if (data === null) return { ok: false, status: 404, error: "Source not found" };
        return { ok: true, data };
    } catch (error) {
        // never log row content, only the error
        console.error("Failed to load Source detail:", error instanceof Error ? error.message : "unknown error");
        return isNotAuthenticatedError(error)
            ? { ok: false, status: 401, error: "Sign in again to view this document." }
            : { ok: false, status: 500, error: "Could not load this tab." };
    }
}

export type SourceOverview = { header: SourceHeader; ocr: OcrSummary };

/** Header facts plus the OCR page count for the History tab note; 404 for anything the caller does not own. */
export async function getSourceOverview(sourceId: number): Promise<DetailResult<SourceOverview>> {
    return detail(sourceId, async ({ userId, workspaceId }) => {
        const header = await getSourceHeader(workspaceId, userId, sourceId);
        if (!header) return null;
        const ocr = (await getOcrSummary(workspaceId, userId, sourceId)) ?? { pages: 0, blocks: 0 };
        return { header, ocr };
    });
}

export async function getSourceStructure(sourceId: number, request?: PageRequest): Promise<DetailResult<Page<StructureBlock>>> {
    return detail(sourceId, ({ userId, workspaceId }) => listStructure(workspaceId, userId, sourceId, request));
}

export async function getSourceChunks(
    sourceId: number,
    request?: PageRequest & { kind?: ChunkKind }
): Promise<DetailResult<Page<ChunkView>>> {
    return detail(sourceId, ({ userId, workspaceId }) => listChunks(workspaceId, userId, sourceId, request));
}

export async function getSourceHistory(sourceId: number, request?: PageRequest): Promise<DetailResult<Page<JobHistoryEntry>>> {
    return detail(sourceId, ({ userId, workspaceId }) => listIngestionHistory(workspaceId, userId, sourceId, request));
}

export async function getSourceVersionsPage(sourceId: number, request?: PageRequest): Promise<DetailResult<Page<SourceVersionView>>> {
    return detail(sourceId, async ({ userId, workspaceId }) => {
        // listSourceVersions yields an empty page for a foreign Source: confirm ownership first so that is a 404
        const header = await getSourceHeader(workspaceId, userId, sourceId);
        if (!header) return null;
        return listSourceVersions(workspaceId, userId, sourceId, request);
    });
}
