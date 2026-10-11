import { notFound } from "next/navigation";
import { resolveWorkspace } from "@/lib/workspace";
import { getSourceHeader } from "@/lib/files/source-detail";
import { isValidSourceId } from "@/lib/files/source-detail-paging";
import FileViewer from "./file-viewer";

/**
 * Source detail. Existence is decided on the server, scoped to the caller's workspace and user: a missing, foreign or
 * soft-deleted Source is a real 404 (indistinguishable from each other), never an empty shell.
 */
export default async function SourceDetailPage({ params }: { params: Promise<{ id: string }> }) {
    const { id } = await params;
    const sourceId = Number(id);
    if (!isValidSourceId(sourceId)) notFound();
    const { userId, workspaceId } = await resolveWorkspace();
    const header = await getSourceHeader(workspaceId, userId, sourceId);
    if (!header) notFound();
    return <FileViewer sourceId={sourceId} />;
}
