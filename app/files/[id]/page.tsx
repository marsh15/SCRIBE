"use client";

import { useState, useEffect } from "react";
import { useParams, useRouter } from "next/navigation";
import { getFileWithChunks } from "./actions";
import { ThreePaneLayout } from "@/components/three-pane-layout";
import { Button } from "@/components/ui/button";
import {
    ArrowLeft,
    FileText,
    File,
    FileSpreadsheet,
    FileArchive,
    Database,
    Hash,
    Clock,
    HardDrive,
    Layers,
    Eye,
    BookOpen,
    ExternalLink,
} from "lucide-react";

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

export default function FileViewer() {
    const params = useParams();
    const router = useRouter();
    const fileId = Number(params.id);

    const [data, setData] = useState<any>(null);
    const [loading, setLoading] = useState(true);
    const [expandedChunk, setExpandedChunk] = useState<number | null>(null);
    const [activeTab, setActiveTab] = useState<"preview" | "chunks">("preview");

    useEffect(() => {
        async function load() {
            const result = await getFileWithChunks(fileId);
            setData(result);
            setLoading(false);
            // Default to chunks tab if no preview available
            if (result?.file && !isPdfFile(result.file.type, result.file.name) && !isTextViewable(result.file.type, result.file.name)) {
                setActiveTab("chunks");
            }
        }
        load();
    }, [fileId]);

    if (loading) {
        return (
            <ThreePaneLayout>
                <div className="flex h-full items-center justify-center">
                    <div className="flex flex-col items-center gap-3">
                        <div className="w-8 h-8 border-2 border-[#00C4A0] border-t-transparent rounded-full animate-spin" />
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
                        <Database className="w-10 h-10 mx-auto text-muted-foreground/40" />
                        <p className="font-mono text-xs text-muted-foreground uppercase tracking-wider">
                            File not found
                        </p>
                        <Button
                            variant="outline"
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
    const canPreview = isPdfFile(file.type, file.name) || isTextViewable(file.type, file.name);

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

                    {/* Tabs */}
                    <div className="flex items-center gap-1 bg-muted/50 rounded-sm p-0.5">
                        {canPreview && (
                            <button
                                onClick={() => setActiveTab("preview")}
                                className={`flex items-center gap-1.5 px-3 py-1.5 rounded-sm font-mono text-[10px] uppercase tracking-wider transition-all ${activeTab === "preview"
                                    ? "bg-background text-foreground shadow-sm"
                                    : "text-muted-foreground hover:text-foreground"
                                    }`}
                            >
                                <Eye className="w-3 h-3" />
                                Preview
                            </button>
                        )}
                        <button
                            onClick={() => setActiveTab("chunks")}
                            className={`flex items-center gap-1.5 px-3 py-1.5 rounded-sm font-mono text-[10px] uppercase tracking-wider transition-all ${activeTab === "chunks"
                                ? "bg-background text-foreground shadow-sm"
                                : "text-muted-foreground hover:text-foreground"
                                }`}
                        >
                            <Layers className="w-3 h-3" />
                            Chunks ({chunks.length})
                        </button>
                    </div>

                    {/* Open in new tab */}
                    {isPdfFile(file.type, file.name) && (
                        <a
                            href={`/api/files/${fileId}/view`}
                            target="_blank"
                            rel="noopener noreferrer"
                            className="flex items-center gap-1.5 text-xs font-mono text-muted-foreground hover:text-[#00C4A0] transition-colors"
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
                                </div>
                            </div>
                        </div>
                    </div>
                </div>

                {/* Tab Content */}
                <div className="flex-1 overflow-hidden">
                    {activeTab === "preview" && canPreview && (
                        <div className="h-full px-6 sm:px-12 pb-4">
                            <div className="max-w-5xl mx-auto h-full">
                                {isPdfFile(file.type, file.name) ? (
                                    /* PDF Viewer — with fallback if no stored data */
                                    extractedText || chunks.length > 0 ? (
                                        <div className="h-full flex flex-col">
                                            <iframe
                                                src={`/api/files/${fileId}/view`}
                                                className="w-full flex-1 rounded-sm border border-border/50 bg-white"
                                                title={`Preview: ${file.name}`}
                                                onError={() => { }}
                                            />
                                            {/* Fallback text view in case iframe shows nothing */}
                                            {!data.hasFileData && (
                                                <div className="mt-3 p-4 rounded-sm border border-amber-500/30 bg-amber-50/50 dark:bg-amber-950/20">
                                                    <p className="font-mono text-[10px] uppercase tracking-wider text-amber-600 dark:text-amber-400 mb-2">
                                                        ⚠ Original file not available — showing extracted text
                                                    </p>
                                                    <p className="text-xs text-muted-foreground">
                                                        This file was uploaded before file preview was enabled. Re-upload to see the full PDF.
                                                        You can still view all indexed chunks in the Chunks tab.
                                                    </p>
                                                </div>
                                            )}
                                        </div>
                                    ) : null
                                ) : (
                                    /* Text Viewer */
                                    <div className="h-full overflow-y-auto rounded-sm border border-border/50 bg-card p-6">
                                        <pre className="font-mono text-sm text-foreground/80 leading-relaxed whitespace-pre-wrap">
                                            {extractedText || chunks.map((c: any) => c.content).join("\n\n")}
                                        </pre>
                                    </div>
                                )}
                            </div>
                        </div>
                    )}

                    {activeTab === "chunks" && (
                        <div className="h-full overflow-y-auto px-6 sm:px-12 pb-8">
                            <div className="max-w-3xl mx-auto space-y-3 pt-2">
                                {/* Section Header */}
                                <div className="flex items-center gap-2 mb-4">
                                    <div className="w-1.5 h-1.5 rounded-full bg-[#00C4A0]" />
                                    <h2 className="font-mono text-[10px] uppercase tracking-widest text-muted-foreground">
                                        Indexed Chunks
                                    </h2>
                                </div>

                                {chunks.map((chunk: any, index: number) => {
                                    const isExpanded = expandedChunk === index;
                                    const content = chunk.content;
                                    const meta = chunk.metadata as any;
                                    const shouldTruncate = content.length > 300 && !isExpanded;
                                    const displayContent = shouldTruncate
                                        ? content.substring(0, 300) + "..."
                                        : content;

                                    return (
                                        <div
                                            key={chunk.id}
                                            className="group rounded-sm border border-border/50 bg-card hover:border-[#00C4A0]/20 transition-all duration-200 cursor-pointer animate-in fade-in slide-in-from-bottom-1 duration-200"
                                            style={{ animationDelay: `${Math.min(index * 30, 300)}ms` }}
                                            onClick={() =>
                                                setExpandedChunk(isExpanded ? null : index)
                                            }
                                        >
                                            {/* Chunk Header */}
                                            <div className="flex items-center justify-between px-4 py-2.5 border-b border-border/30">
                                                <div className="flex items-center gap-3">
                                                    <div className="flex items-center gap-1.5">
                                                        <Hash className="w-3 h-3 text-[#00C4A0]" />
                                                        <span className="font-mono text-[10px] uppercase tracking-widest text-muted-foreground">
                                                            Chunk {index + 1} of {chunks.length}
                                                        </span>
                                                    </div>
                                                    {meta?.estimatedPage && (
                                                        <span className="font-mono text-[10px] px-1.5 py-0.5 rounded-sm bg-muted text-muted-foreground">
                                                            Page ~{meta.estimatedPage}{meta.totalPages ? `/${meta.totalPages}` : ''}
                                                        </span>
                                                    )}
                                                    {meta?.section && (
                                                        <span className="font-mono text-[10px] px-1.5 py-0.5 rounded-sm bg-muted text-muted-foreground">
                                                            §{meta.section}
                                                        </span>
                                                    )}
                                                </div>
                                                <span className="font-mono text-[10px] text-muted-foreground/60">
                                                    {content.length} chars
                                                </span>
                                            </div>

                                            {/* Chunk Content */}
                                            <div className="px-4 py-3">
                                                <p className="font-sans text-sm text-foreground/80 leading-relaxed whitespace-pre-wrap">
                                                    {displayContent}
                                                </p>
                                                {content.length > 300 && (
                                                    <button className="mt-2 font-mono text-[10px] uppercase tracking-wider text-[#00C4A0] hover:text-[#00C4A0]/80 transition-colors">
                                                        {isExpanded ? "Show less" : "Show more"}
                                                    </button>
                                                )}
                                            </div>
                                        </div>
                                    );
                                })}
                            </div>
                        </div>
                    )}
                </div>
            </div>
        </ThreePaneLayout>
    );
}
