import { db } from "@/lib/db-config";
import { files } from "@/lib/db-schema";
import { eq, and, isNull } from "drizzle-orm";
import { resolveWorkspace } from "@/lib/workspace";
import { isNotAuthenticatedError } from "@/lib/auth";
import { downloadBlobToBuffer } from "@/lib/storage/blob";
import { resolveVersionBlob } from "@/lib/files/source-detail";
import { inlineDisposition } from "@/lib/files/content-disposition";

export async function GET(
    req: Request,
    { params }: { params: Promise<{ id: string }> }
) {
    try {
        const { userId, workspaceId } = await resolveWorkspace();
        const { id } = await params;
        const fileId = Number(id);

        // T24: a specific version. A duplicate version (T19) has no blob of its own, so it is served from the version
        // it duplicates; the lookup is scoped to the caller's workspace/user and 404s for foreign or deleted Sources.
        const versionParam = new URL(req.url).searchParams.get("version");
        if (versionParam !== null) {
            const versionNumber = Number(versionParam);
            if (!Number.isInteger(versionNumber) || versionNumber <= 0) {
                return new Response("File not found", { status: 404 });
            }
            const blob = await resolveVersionBlob(workspaceId, userId, fileId, versionNumber);
            if (!blob) return new Response("File not found", { status: 404 });
            const bytes = await downloadBlobToBuffer(blob.url);
            return new Response(bytes, {
                headers: {
                    "Content-Type": blob.mimeType,
                    "Content-Disposition": inlineDisposition(blob.fileName),
                    "Cache-Control": "private, max-age=3600",
                },
            });
        }

        const file = await db.query.files.findFirst({
            where: and(eq(files.id, fileId), eq(files.userId, userId), eq(files.workspaceId, workspaceId), isNull(files.deletedAt)),
            columns: {
                fileData: true,
                storageUrl: true,
                type: true,
                name: true,
            },
        });

        if (!file) {
            return new Response("File not found", { status: 404 });
        }

        if (file.storageUrl) {
            const blobBuffer = await downloadBlobToBuffer(file.storageUrl);
            return new Response(blobBuffer, {
                headers: {
                    "Content-Type": file.type || "application/octet-stream",
                    "Content-Disposition": inlineDisposition(file.name),
                    "Cache-Control": "private, max-age=3600",
                },
            });
        }

        if (!file.fileData) {
            return new Response("File data unavailable", { status: 404 });
        }

        // Parse data URI: data:mime;base64,DATA
        const match = file.fileData.match(/^data:(.+);base64,(.+)$/);
        if (!match) {
            return new Response("Invalid file data", { status: 500 });
        }

        const mimeType = match[1];
        const base64Data = match[2];
        const binaryData = Buffer.from(base64Data, "base64");

        return new Response(binaryData, {
            headers: {
                "Content-Type": mimeType,
                "Content-Disposition": inlineDisposition(file.name),
                "Cache-Control": "private, max-age=3600",
            },
        });
    } catch (error) {
        if (isNotAuthenticatedError(error)) return new Response("Not authenticated", { status: 401 });
        console.error("File serve error:", error);
        return new Response("Failed to serve file", { status: 500 });
    }
}
