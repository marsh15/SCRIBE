import { db } from "@/lib/db-config";
import { files } from "@/lib/db-schema";
import { eq, and } from "drizzle-orm";
import { getUserId } from "@/lib/auth";

export async function GET(
    req: Request,
    { params }: { params: Promise<{ id: string }> }
) {
    try {
        const userId = await getUserId();
        const { id } = await params;
        const fileId = Number(id);

        const file = await db.query.files.findFirst({
            where: and(eq(files.id, fileId), eq(files.userId, userId)),
            columns: {
                fileData: true,
                type: true,
                name: true,
            },
        });

        if (!file || !file.fileData) {
            return new Response("File not found", { status: 404 });
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
                "Content-Disposition": `inline; filename="${file.name}"`,
                "Cache-Control": "private, max-age=3600",
            },
        });
    } catch (error) {
        console.error("File serve error:", error);
        return new Response("Failed to serve file", { status: 500 });
    }
}
