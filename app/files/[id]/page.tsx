"use client";

import { useState, useEffect } from "react";
import { useParams, useRouter } from "next/navigation";
import { getFileWithChunks } from "./actions";
import { ThreePaneLayout } from "@/components/three-pane-layout";
import { Button } from "@/components/ui/button";
import { ScrollArea } from "@/components/ui/scroll-area";
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

export default function FileViewer() {
    const params = useParams();
    const router = useRouter();
    const fileId = Number(params.id);

    const [data, setData] = useState<any>(null);
    const [loading, setLoading] = useState(true);
    const [expandedChunk, setExpandedChunk] = useState<number | null>(null);

    useEffect(() => {
        async function load() {
            const result = await getFileWithChunks(fileId);
            setData(result);
            setLoading(false);
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
                            onClick={() => router.push("/upload")}
                        >
                            <ArrowLeft className="w-3 h-3 mr-2" /> Back to uploads
                        </Button>
                    </div>
                </div>
            </ThreePaneLayout>
        );
    }

    const { file, chunks } = data;

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
                            onClick={() => router.push("/upload")}
                        >
                            <ArrowLeft className="w-3.5 h-3.5" />
                        </Button>
                        <div className="flex items-center gap-2 text-xs font-mono text-muted-foreground uppercase tracking-wider">
                            <Database className="w-3 h-3" />
                            Document Viewer
                        </div>
                    </div>
                </div>

                {/* Content */}
                <div className="flex-1 overflow-y-auto px-6 sm:px-12">
                    <div className="max-w-3xl mx-auto py-8 space-y-8">
                        {/* File Info Header */}
                        <div className="flex items-start gap-4 p-5 rounded-sm border border-border/50 bg-card animate-in fade-in slide-in-from-top-2 duration-300">
                            <div className="w-12 h-12 rounded-sm border border-border/50 bg-muted/50 flex items-center justify-center shrink-0">
                                {getFileIcon(file.type, file.name)}
                            </div>
                            <div className="flex-1 min-w-0">
                                <h1 className="font-serif text-xl text-foreground truncate mb-2">
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

                        {/* Section Header */}
                        <div className="flex items-center gap-2">
                            <div className="w-1.5 h-1.5 rounded-full bg-[#00C4A0]" />
                            <h2 className="font-mono text-[10px] uppercase tracking-widest text-muted-foreground">
                                Indexed Chunks
                            </h2>
                        </div>

                        {/* Chunks */}
                        <div className="space-y-3">
                            {chunks.map((chunk: any, index: number) => {
                                const isExpanded = expandedChunk === index;
                                const content = chunk.content;
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
                                            <div className="flex items-center gap-2">
                                                <Hash className="w-3 h-3 text-[#00C4A0]" />
                                                <span className="font-mono text-[10px] uppercase tracking-widest text-muted-foreground">
                                                    Chunk {index + 1} of {chunks.length}
                                                </span>
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
                </div>
            </div>
        </ThreePaneLayout>
    );
}
