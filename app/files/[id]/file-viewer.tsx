"use client";

import { useState, useEffect, useCallback } from "react";
import { useRouter } from "next/navigation";
import {
    getFileWithChunks,
    getSourceChunks,
    getSourceHistory,
    getSourceOverview,
    getSourceStructure,
    getSourceVersionsPage,
    type SourceOverview,
} from "./actions";
import { SourceTabs } from "@/components/source-tabs";
import { ChunksPanel, HistoryPanel, StructurePanel, VersionsPanel } from "@/components/source-detail-panels";
import type { ChunkKind } from "@/lib/files/source-detail";
import { ThreePaneLayout } from "@/components/three-pane-layout";
import { Button } from "@/components/ui/button";
import {
    ArrowLeft,
    FileText,
    File,
    FileSpreadsheet,
    FileArchive,
    Database,
    Clock,
    HardDrive,
    Layers,
    Eye,
    ListTree,
    History,
    GitBranch,
    ExternalLink,
    AlertCircle,
    Loader2,
    RefreshCw,
} from "lucide-react";
import {
    canOpenOriginalPdfFile,
    getMissingOriginalFileMessage,
} from "@/lib/files/preview";

function getFileIcon(type: string, name: string) {
    const ext = name.split(".").pop()?.toLowerCase();
    if (type === "application/pdf" || ext === "pdf")
        return <FileText className="w-5 h-5 text-red-400" />;
    if (type === "text/csv" || ext === "csv")
        return <FileSpreadsheet className="w-5 h-5 text-green-400" />;
    if (ext === "docx" || ext === "doc")
        return <File className="w-5 h-5 text-blue-400" />;
    if (ext === "md" || ext === "txt")
        return <FileText className="w-5 h-5 text-orange-300" />;
    return <FileArchive className="w-5 h-5 text-muted-foreground" />;
}

function formatFileSize(bytes: number) {
    if (bytes < 1024) return `${bytes} B`;
    if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
    return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

function formatDate(dateStr: string) {
    return new Date(dateStr).toLocaleDateString("en-US", {
        month: "short",
        day: "numeric",
        year: "numeric",
        hour: "2-digit",
        minute: "2-digit",
    });
}

function isPdfFile(type: string, name: string) {
    return type === "application/pdf" || name.toLowerCase().endsWith(".pdf");
}

function isTextViewable(type: string, name: string) {
    const ext = name.split(".").pop()?.toLowerCase();
    return ["txt", "md", "csv"].includes(ext || "") ||
        type.startsWith("text/");
}

function isPendingStatus(status: string) {
    return ["uploading", "queued", "processing", "retrying"].includes(status);
}

function canProcessStatus(status: string) {
    return status === "queued" || status === "retrying";
}

function getStatusCopy(status: string) {
    switch (status) {
        case "uploading":
            return {
                title: "Upload still settling",
                body: "Scribe is waiting for the private upload to finish before indexing can begin.",
                action: "Refresh status",
            };
        case "queued":
            return {
                title: "Ready to index",
                body: "This Source is queued for extraction, chunking, and embeddings.",
                action: "Process now",
            };
        case "processing":
            return {
                title: "Indexing in progress",
                body: "Scribe is extracting text, chunking the document, and creating embeddings. This view refreshes while it works.",
                action: "Refresh status",
            };
        case "retrying":
            return {
                title: "Indexing will retry",
                body: "A previous attempt failed. You can start the next attempt now or wait for the queue.",
                action: "Process now",
            };
        case "failed":
            return {
                title: "Indexing failed",
                body: "Scribe could not finish indexing this Source. Check the error below, then re-upload if the file is invalid or unsupported.",
                action: "Refresh status",
            };
        default:
            return {
                title: "Document ready",
                body: "This Source is indexed and available for retrieval.",
                action: "Refresh status",
            };
    }
}

function getNoticeClass(tone: "success" | "error" | "muted") {
    if (tone === "success") return "text-rag";
    if (tone === "error") return "text-destructive/80";
    return "text-muted-foreground";
}

type TabId = "original" | "structure" | "chunks" | "versions" | "history";

export default function FileViewer({ sourceId }: { sourceId: number }) {
    const router = useRouter();
    const fileId = sourceId;

    const [data, setData] = useState<any>(null);
    const [loading, setLoading] = useState(true);
    const [loadError, setLoadError] = useState<string | null>(null);
    const [statusNotice, setStatusNotice] = useState<{
        text: string;
        tone: "success" | "error" | "muted";
    } | null>(null);
    const [processingNow, setProcessingNow] = useState(false);
    const [activeTab, setActiveTab] = useState<TabId>("original");
    const [overview, setOverview] = useState<SourceOverview | null>(null);

    const loadFile = useCallback(async (options?: { silent?: boolean }) => {
        if (!Number.isInteger(fileId) || fileId <= 0) {
            setData(null);
            setLoadError("Invalid file id.");
            setLoading(false);
            return null;
        }

        if (!options?.silent) {
            setLoading(true);
        }

        try {
            const result = await getFileWithChunks(fileId);
            if (result && "error" in result) {
                setData(null);
                setLoadError(result.error ?? "Could not load this document.");
                return null;
            }

            setData(result);
            setLoadError(null);

            if (
                result?.file &&
                !isPdfFile(result.file.type, result.file.name) &&
                !isTextViewable(result.file.type, result.file.name)
            ) {
                setActiveTab((tab) => (tab === "original" ? "structure" : tab));
            }

            return result;
        } catch (error) {
            const message = error instanceof Error
                ? error.message
                : "Could not load this document.";
            setLoadError(message);
            setData(null);
            return null;
        } finally {
            setLoading(false);
        }
    }, [fileId]);

    useEffect(() => {
        void loadFile();
    }, [loadFile]);

    const fileStatusForOverview = data?.file?.status;
    useEffect(() => {
        let cancelled = false;
        void getSourceOverview(fileId).then((result) => {
            if (!cancelled && result.ok) setOverview(result.data);
        });
        return () => {
            cancelled = true;
        };
    }, [fileId, fileStatusForOverview]);

    const loadStructure = useCallback((offset: number) => getSourceStructure(fileId, { offset }), [fileId]);
    const makeChunkLoader = useCallback(
        (kind: ChunkKind) => (offset: number) => getSourceChunks(fileId, { offset, kind }),
        [fileId]
    );
    const loadVersions = useCallback((offset: number) => getSourceVersionsPage(fileId, { offset }), [fileId]);
    const loadHistory = useCallback((offset: number) => getSourceHistory(fileId, { offset }), [fileId]);

    useEffect(() => {
        const status = data?.file?.status || "ready";
        if (!isPendingStatus(status)) return;

        const interval = setInterval(() => {
            void loadFile({ silent: true });
        }, 5000);

        return () => clearInterval(interval);
    }, [data?.file?.status, loadFile]);

    async function handleProcessNow() {
        setProcessingNow(true);
        setStatusNotice(null);

        try {
            const response = await fetch("/api/sources/process-now", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ sourceId: fileId }),
            });
            const result = (await response.json()) as {
                error?: string;
                claimed?: number;
                ready?: number;
                retrying?: number;
                failed?: number;
            };

            if (!response.ok) {
                throw new Error(result.error || "Could not start indexing.");
            }

            const notice =
                (result.ready ?? 0) > 0
                    ? { text: "Source indexed and ready to cite.", tone: "success" as const }
                    : (result.failed ?? 0) > 0
                        ? { text: "Indexing ran but failed. The latest error is shown below.", tone: "error" as const }
                        : (result.retrying ?? 0) > 0
                            ? { text: "Indexing ran but will retry after the current error.", tone: "muted" as const }
                            : (result.claimed ?? 0) > 0
                                ? { text: "Indexing started. This page will refresh while it finishes.", tone: "muted" as const }
                                : { text: "No queued work was ready yet. Scribe will keep checking.", tone: "muted" as const };

            setStatusNotice(notice);
            await loadFile({ silent: true });
        } catch (error) {
            setStatusNotice({
                text: error instanceof Error ? error.message : "Could not start indexing.",
                tone: "error",
            });
        } finally {
            setProcessingNow(false);
        }
    }

    if (loading) {
        return (
            <ThreePaneLayout>
                <div className="flex h-full items-center justify-center">
                    <div className="flex flex-col items-center gap-3">
                        <div className="w-8 h-8 border-2 border-rag border-t-transparent rounded-full animate-spin" />
                        <p className="font-mono text-xs text-muted-foreground uppercase tracking-wider">
                            Loading document...
                        </p>
                    </div>
                </div>
            </ThreePaneLayout>
        );
    }

    if (!data) {
        return (
            <ThreePaneLayout>
                <div className="flex h-full items-center justify-center">
                    <div className="text-center space-y-3">
                        {loadError ? (
                            <AlertCircle className="w-10 h-10 mx-auto text-destructive/70" />
                        ) : (
                            <Database className="w-10 h-10 mx-auto text-muted-foreground/40" />
                        )}
                        <p className="font-mono text-xs text-muted-foreground uppercase tracking-wider">
                            {loadError ? "Could not load document" : "File not found"}
                        </p>
                        {loadError && (
                            <p className="max-w-md text-sm text-muted-foreground">
                                {loadError}
                            </p>
                        )}
                        {loadError && (
                            <Button
                                variant="outline"
                                size="sm"
                                className="font-mono text-xs uppercase tracking-wider"
                                onClick={() => void loadFile()}
                            >
                                <RefreshCw className="w-3 h-3 mr-2" /> Retry
                            </Button>
                        )}
                        <Button
                            variant={loadError ? "ghost" : "outline"}
                            size="sm"
                            className="font-mono text-xs uppercase tracking-wider"
                            onClick={() => router.back()}
                        >
                            <ArrowLeft className="w-3 h-3 mr-2" /> Go back
                        </Button>
                    </div>
                </div>
            </ThreePaneLayout>
        );
    }

    const { file, chunks, extractedText } = data;
    const fileStatus = file.status || "ready";
    const statusCopy = getStatusCopy(fileStatus);
    const canPreview = isPdfFile(file.type, file.name) || isTextViewable(file.type, file.name);
    const canOpenOriginalPdf = canOpenOriginalPdfFile({
        isPdf: isPdfFile(file.type, file.name),
        hasFileData: data.hasFileData,
        hasStorageUrl: data.hasStorageUrl,
    });
    const previewText = extractedText || chunks.map((c: any) => c.content).join("\n\n");

    const originalPanel = canPreview ? (

                        <div className="h-full px-6 sm:px-12 pb-4">
                            <div className="max-w-5xl mx-auto h-full">
                                {fileStatus !== "ready" ? (
                                    <div className="h-full rounded-sm border border-border/50 bg-card p-6 flex items-center justify-center">
                                        <div className="max-w-md text-center space-y-3">
                                            {fileStatus === "processing" ? (
                                                <Loader2 className="w-8 h-8 mx-auto animate-spin text-rag" />
                                            ) : (
                                                <AlertCircle className={`w-8 h-8 mx-auto ${fileStatus === "failed"
                                                    ? "text-destructive/70"
                                                    : "text-muted-foreground/60"
                                                    }`} />
                                            )}
                                            <div>
                                                <p className="font-mono text-xs uppercase tracking-wider text-foreground">
                                                    {statusCopy.title}
                                                </p>
                                                <p className="mt-2 text-sm text-muted-foreground">
                                                    {statusCopy.body}
                                                </p>
                                            </div>
                                            {file.processingError && (
                                                <p className="rounded-sm border border-border/50 bg-muted/40 p-3 text-left font-mono text-[11px] text-muted-foreground">
                                                    {file.processingError}
                                                </p>
                                            )}
                                            <Button
                                                variant={canProcessStatus(fileStatus) ? "outline" : "ghost"}
                                                size="sm"
                                                className="h-8 rounded-sm px-2 text-[10px] font-mono uppercase tracking-wider"
                                                onClick={
                                                    canProcessStatus(fileStatus)
                                                        ? handleProcessNow
                                                        : () => void loadFile({ silent: true })
                                                }
                                                disabled={processingNow}
                                            >
                                                {processingNow ? (
                                                    <Loader2 className="mr-1.5 h-3 w-3 animate-spin" />
                                                ) : (
                                                    <RefreshCw className="mr-1.5 h-3 w-3" />
                                                )}
                                                {statusCopy.action}
                                            </Button>
                                        </div>
                                    </div>
                                ) : isPdfFile(file.type, file.name) ? (
                                    previewText ? (
                                        canOpenOriginalPdf ? (
                                            <div className="h-full flex flex-col">
                                            <iframe
                                                src={`/api/files/${fileId}/view`}
                                                className="w-full flex-1 rounded-sm border border-border/50 bg-white"
                                                title={`Preview: ${file.name}`}
                                                onError={() => { }}
                                            />
                                            {!data.hasFileData && data.hasStorageUrl && (
                                                <div className="mt-3 p-4 rounded-sm border border-amber-500/30 bg-amber-50/50 dark:bg-amber-950/20">
                                                    <p className="font-mono text-[10px] uppercase tracking-wider text-amber-600 dark:text-amber-400 mb-2">
                                                        Preview streamed from object storage
                                                    </p>
                                                </div>
                                            )}
                                        </div>
                                        ) : (
                                            <div className="h-full overflow-y-auto rounded-sm border border-border/50 bg-card p-6">
                                                <div className="mb-4 rounded-sm border border-amber-500/30 bg-amber-50/50 p-4 dark:bg-amber-950/20">
                                                    <p className="font-mono text-[10px] uppercase tracking-wider text-amber-600 dark:text-amber-400">
                                                        Original PDF not available
                                                    </p>
                                                    <p className="mt-2 text-xs text-muted-foreground">
                                                        {getMissingOriginalFileMessage(file.name)}
                                                    </p>
                                                </div>
                                                <pre className="font-mono text-sm text-foreground/80 leading-relaxed whitespace-pre-wrap">
                                                    {previewText}
                                                </pre>
                                            </div>
                                        )
                                    ) : null
                                ) : (
                                    /* Text Viewer */
                                    <div className="h-full overflow-y-auto rounded-sm border border-border/50 bg-card p-6">
                                        <pre className="font-mono text-sm text-foreground/80 leading-relaxed whitespace-pre-wrap">
                                            {previewText}
                                        </pre>
                                    </div>
                                )}
                            </div>
                        </div>
    ) : (
        <div className="h-full px-6 py-4 sm:px-12">
            <div className="mx-auto max-w-5xl rounded-sm border border-border/50 bg-card p-6 text-sm text-muted-foreground">
                <p>No inline preview for this file type.</p>
                {(data.hasFileData || data.hasStorageUrl) && (
                    <a
                        href={`/api/files/${fileId}/view`}
                        target="_blank"
                        rel="noopener noreferrer"
                        className="mt-2 inline-flex min-h-11 items-center text-foreground underline underline-offset-2 outline-none focus-visible:ring-2 focus-visible:ring-ring"
                    >
                        Open the original file
                    </a>
                )}
            </div>
        </div>
    );

    return (
        <ThreePaneLayout>
            <div className="flex flex-col h-full bg-background">
                {/* Header Bar */}
                <div className="h-12 border-b border-border/50 flex items-center justify-between px-6 shrink-0 bg-background/95 backdrop-blur z-10">
                    <div className="flex items-center gap-3">
                        <Button
                            variant="ghost"
                            size="icon"
                            className="h-7 w-7 rounded-sm hover:bg-muted transition-colors"
                            onClick={() => router.back()}
                        >
                            <ArrowLeft className="w-3.5 h-3.5" />
                        </Button>
                        <div className="flex items-center gap-2 text-xs font-mono text-muted-foreground uppercase tracking-wider">
                            <Database className="w-3 h-3" />
                            Document Viewer
                        </div>
                    </div>

                    {/* Open in new tab */}
                    {canOpenOriginalPdf && (
                        <a
                            href={`/api/files/${fileId}/view`}
                            target="_blank"
                            rel="noopener noreferrer"
                            className="flex items-center gap-1.5 text-xs font-mono text-muted-foreground hover:text-rag transition-colors"
                        >
                            <ExternalLink className="w-3 h-3" />
                            Open PDF
                        </a>
                    )}
                </div>

                {/* File Info Header */}
                <div className="px-6 sm:px-12 pt-4 pb-2">
                    <div className="max-w-5xl mx-auto">
                        <div className="flex items-start gap-4 p-4 rounded-sm border border-border/50 bg-card animate-in fade-in slide-in-from-top-2 duration-300">
                            <div className="w-10 h-10 rounded-sm border border-border/50 bg-muted/50 flex items-center justify-center shrink-0">
                                {getFileIcon(file.type, file.name)}
                            </div>
                            <div className="flex-1 min-w-0">
                                <h1 className="font-serif text-lg text-foreground truncate mb-1.5">
                                    {file.name}
                                </h1>
                                <div className="flex flex-wrap items-center gap-4 text-xs font-mono text-muted-foreground">
                                    <span className="flex items-center gap-1.5">
                                        <HardDrive className="w-3 h-3" />
                                        {formatFileSize(file.size)}
                                    </span>
                                    <span className="flex items-center gap-1.5">
                                        <Clock className="w-3 h-3" />
                                        {formatDate(file.createdAt)}
                                    </span>
                                    <span className="flex items-center gap-1.5">
                                        <Layers className="w-3 h-3" />
                                        {chunks.length} chunk{chunks.length !== 1 ? "s" : ""}
                                    </span>
                                    <span className={`px-1.5 py-0.5 rounded-sm uppercase ${fileStatus === "ready"
                                        ? "bg-rag/15 text-rag"
                                        : fileStatus === "failed"
                                            ? "bg-destructive/10 text-destructive"
                                            : fileStatus === "retrying"
                                                ? "bg-accent/15 text-accent"
                                            : "bg-muted text-muted-foreground"
                                        }`}>
                                        {fileStatus}
                                    </span>
                                </div>
                                {fileStatus !== "ready" && (
                                    <p className="mt-2 text-xs text-muted-foreground">
                                        {statusCopy.body}
                                    </p>
                                )}
                                {fileStatus !== "ready" && file.processingError && (
                                    <p className={`mt-2 text-xs ${fileStatus === "failed"
                                        ? "text-destructive/80"
                                        : "text-muted-foreground"
                                        }`}>
                                        {file.processingError}
                                    </p>
                                )}
                                {statusNotice && (
                                    <p className={`mt-2 text-xs ${getNoticeClass(statusNotice.tone)}`}>
                                        {statusNotice.text}
                                    </p>
                                )}
                            </div>
                            {fileStatus !== "ready" && (
                                <Button
                                    variant={canProcessStatus(fileStatus) ? "outline" : "ghost"}
                                    size="sm"
                                    className="h-8 shrink-0 rounded-sm px-2 text-[10px] font-mono uppercase tracking-wider"
                                    onClick={
                                        canProcessStatus(fileStatus)
                                            ? handleProcessNow
                                            : () => void loadFile({ silent: true })
                                    }
                                    disabled={processingNow}
                                >
                                    {processingNow ? (
                                        <Loader2 className="mr-1.5 h-3 w-3 animate-spin" />
                                    ) : (
                                        <RefreshCw className="mr-1.5 h-3 w-3" />
                                    )}
                                    {statusCopy.action}
                                </Button>
                            )}
                        </div>
                    </div>
                </div>

                <SourceTabs
                    label="Source detail"
                    activeId={activeTab}
                    onChange={(id) => setActiveTab(id as TabId)}
                    tabs={[
                        { id: "original", label: "Original", icon: <Eye className="h-4 w-4" aria-hidden />, render: () => originalPanel },
                        {
                            id: "structure",
                            label: "Structure",
                            icon: <ListTree className="h-4 w-4" aria-hidden />,
                            render: () => <StructurePanel load={loadStructure} />,
                        },
                        {
                            id: "chunks",
                            label: "Chunks",
                            icon: <Layers className="h-4 w-4" aria-hidden />,
                            render: () => <ChunksPanel makeLoader={makeChunkLoader} legacyChunks={chunks} />,
                        },
                        {
                            id: "versions",
                            label: "Versions",
                            icon: <GitBranch className="h-4 w-4" aria-hidden />,
                            render: () => <VersionsPanel load={loadVersions} sourceId={fileId} />,
                        },
                        {
                            id: "history",
                            label: "History",
                            icon: <History className="h-4 w-4" aria-hidden />,
                            render: () => (
                                <HistoryPanel
                                    load={loadHistory}
                                    ocrPages={overview?.ocr.pages ?? 0}
                                    reuploadRequiredAt={overview?.header.reuploadRequiredAt ?? null}
                                    reuploadReason={overview?.header.reuploadReason ?? null}
                                />
                            ),
                        },
                    ]}
                />
            </div>
        </ThreePaneLayout>
    );
}
