"use client";

import { useState, useCallback, useRef, useEffect } from "react";
import { getFiles, deleteFile } from "@/app/files/actions";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import {
  Loader2,
  Database,
  UploadCloud,
  FileText,
  File,
  FileSpreadsheet,
  FileArchive,
  Trash2,
  CheckCircle2,
  AlertCircle,
  X,
} from "lucide-react";
import { ThreePaneLayout } from "@/components/three-pane-layout";

function getFileIcon(type: string, name: string) {
  const ext = name.split(".").pop()?.toLowerCase();
  if (type === "application/pdf" || ext === "pdf")
    return <FileText className="w-4 h-4 text-red-400" />;
  if (type === "text/csv" || ext === "csv")
    return <FileSpreadsheet className="w-4 h-4 text-green-400" />;
  if (ext === "docx" || ext === "doc")
    return <File className="w-4 h-4 text-blue-400" />;
  if (ext === "md" || ext === "txt")
    return <FileText className="w-4 h-4 text-orange-300" />;
  return <FileArchive className="w-4 h-4 text-muted-foreground" />;
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
    hour: "2-digit",
    minute: "2-digit",
  });
}

export default function DocumentUpload() {
  const [isLoading, setIsLoading] = useState(false);
  const [isDragging, setIsDragging] = useState(false);
  const [message, setMessage] = useState<{
    type: "error" | "success";
    text: string;
  } | null>(null);
  const [files, setFiles] = useState<any[]>([]);
  const [deletingId, setDeletingId] = useState<number | null>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);

  const fetchFiles = useCallback(async () => {
    const data = await getFiles();
    setFiles(data);
  }, []);

  useEffect(() => {
    fetchFiles();
  }, [fetchFiles]);

  useEffect(() => {
    const hasPending = files.some((file) => file.status === "queued" || file.status === "processing");
    if (!hasPending) return;

    const interval = setInterval(() => {
      fetchFiles().catch(() => {
        // noop
      });
    }, 5000);

    return () => clearInterval(interval);
  }, [files, fetchFiles]);

  const processFile = async (file: File) => {
    setIsLoading(true);
    setMessage(null);

    try {
      const signRes = await fetch("/api/uploads/sign", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          fileName: file.name,
          fileSize: file.size,
          fileType: file.type || "application/octet-stream",
        }),
      });

      const signJson = (await signRes.json()) as {
        ok?: boolean;
        uploadToken?: string;
        error?: string;
      };

      if (!signRes.ok || !signJson.uploadToken) {
        setMessage({
          type: "error",
          text: signJson.error || "Upload signing failed",
        });
        return;
      }

      const uploadForm = new FormData();
      uploadForm.append("file", file);
      uploadForm.append("uploadToken", signJson.uploadToken);

      const completeRes = await fetch("/api/uploads/complete", {
        method: "POST",
        body: uploadForm,
      });

      const completeJson = (await completeRes.json()) as {
        ok?: boolean;
        error?: string;
        file?: { status?: string; id?: number };
      };

      if (!completeRes.ok || !completeJson.ok) {
        setMessage({
          type: "error",
          text: completeJson.error || "Failed to queue upload",
        });
        return;
      }

      setMessage({
        type: "success",
        text:
          completeJson.file?.status === "queued"
            ? "File uploaded and queued for indexing. Processing will continue in background."
            : "File uploaded successfully.",
      });

      // Best-effort worker trigger for non-cron environments
      fetch("/api/internal/ingest/run?limit=1", { method: "POST" }).catch(() => {
        // no-op; ingestion may be driven by cron
      });

      if (fileInputRef.current) fileInputRef.current.value = "";
      await fetchFiles();
    } catch {
      setMessage({
        type: "error",
        text: "An error occurred while processing the document",
      });
    } finally {
      setIsLoading(false);
    }
  };

  const handleFileUpload = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (!file) return;
    await processFile(file);
  };

  const handleDrop = async (e: React.DragEvent) => {
    e.preventDefault();
    setIsDragging(false);
    const file = e.dataTransfer.files?.[0];
    if (!file) return;
    await processFile(file);
  };

  const handleDeleteFile = async (id: number) => {
    setDeletingId(id);
    await deleteFile(id);
    await fetchFiles();
    setDeletingId(null);
  };

  const handleClearAll = async () => {
    if (
      !confirm(
        "Delete ALL files from your knowledge base?\nThis will remove all documents and their embeddings. This cannot be undone.",
      )
    )
      return;
    for (const file of files) {
      await deleteFile(file.id);
    }
    await fetchFiles();
  };

  return (
    <ThreePaneLayout>
      <div className="flex flex-col h-full relative bg-background">
        {/* Header Bar */}
        <div className="h-12 border-b border-border/50 flex items-center justify-between px-6 shrink-0 bg-background/95 backdrop-blur z-10">
          <div className="flex items-center gap-2 text-xs font-mono text-muted-foreground uppercase tracking-wider">
            <Database className="w-3 h-3" />
            Ingestion Engine
          </div>
          {files.length > 0 && (
            <span className="font-mono text-[10px] text-muted-foreground uppercase tracking-wider">
              {files.length} file{files.length !== 1 ? "s" : ""} indexed
            </span>
          )}
        </div>

        {/* Main Content */}
        <div className="flex-1 overflow-y-auto px-6 sm:px-12">
          <div className="max-w-2xl mx-auto py-10 space-y-10">
            {/* Upload Section */}
            <section>
              <div className="text-center mb-8">
                <h1 className="font-serif text-3xl text-foreground mb-2 tracking-tight">
                  Knowledge Base
                </h1>
                <p className="font-sans text-sm text-muted-foreground max-w-md mx-auto">
                  Upload documents to build your searchable knowledge base.
                  Files are chunked, embedded, and indexed for semantic
                  retrieval.
                </p>
              </div>

              {/* Drag & Drop Zone */}
              <div
                onDragOver={(e) => {
                  e.preventDefault();
                  setIsDragging(true);
                }}
                onDragLeave={() => setIsDragging(false)}
                onDrop={handleDrop}
                onClick={() => !isLoading && fileInputRef.current?.click()}
                className={`
                                    relative border-2 border-dashed rounded-md p-10 text-center cursor-pointer transition-all duration-300
                                    ${isDragging
                    ? "border-[#00C4A0] bg-[#00C4A0]/5 scale-[1.01]"
                    : "border-border/60 hover:border-[#00C4A0]/40 hover:bg-muted/30"
                  }
                                    ${isLoading ? "pointer-events-none opacity-60" : ""}
                                `}
              >
                <input
                  ref={fileInputRef}
                  type="file"
                  accept=".pdf,.txt,.md,.csv,.docx"
                  onChange={handleFileUpload}
                  onClick={(e) => e.stopPropagation()}
                  disabled={isLoading}
                  className="hidden"
                />

                {isLoading ? (
                  <div className="flex flex-col items-center gap-4 py-4">
                    <Loader2 className="w-8 h-8 animate-spin text-[#00C4A0]" />
                    <div>
                      <p className="font-mono text-xs uppercase tracking-wider text-foreground">
                        Processing Document
                      </p>
                      <p className="font-mono text-[10px] text-muted-foreground mt-1">
                        Extracting → Chunking → Embedding → Indexing
                      </p>
                    </div>
                    {/* Progress animation */}
                    <div className="w-48 h-1 bg-muted rounded-full overflow-hidden">
                      <div
                        className="h-full bg-[#00C4A0] rounded-full animate-pulse"
                        style={{ width: "60%" }}
                      />
                    </div>
                  </div>
                ) : (
                  <div className="flex flex-col items-center gap-3 py-4">
                    <div
                      className={`
                                            w-14 h-14 rounded-full border-2 flex items-center justify-center transition-all
                                            ${isDragging ? "border-[#00C4A0] bg-[#00C4A0]/10" : "border-border bg-card"}
                                        `}
                    >
                      <UploadCloud
                        className={`w-6 h-6 transition-colors ${isDragging ? "text-[#00C4A0]" : "text-muted-foreground"}`}
                      />
                    </div>
                    <div>
                      <p className="font-sans text-sm text-foreground">
                        <span className="text-[#00C4A0] font-medium">
                          Click to upload
                        </span>{" "}
                        or drag and drop
                      </p>
                      <p className="font-mono text-[10px] text-muted-foreground mt-1.5 uppercase tracking-wider">
                        PDF • TXT • MD • CSV • DOCX — Up to 100MB (plan-based)
                      </p>
                    </div>
                  </div>
                )}
              </div>

              {/* Status Messages */}
              {message && (
                <div className="mt-4 animate-in fade-in slide-in-from-bottom-2 duration-300">
                  <Alert
                    variant={
                      message.type === "error" ? "destructive" : "default"
                    }
                    className={`rounded-sm border relative ${message.type === "success" ? "border-[#00C4A0]/30 bg-[#00C4A0]/5" : ""}`}
                  >
                    <div className="flex items-start gap-2">
                      {message.type === "success" ? (
                        <CheckCircle2 className="w-4 h-4 text-[#00C4A0] shrink-0 mt-0.5" />
                      ) : (
                        <AlertCircle className="w-4 h-4 shrink-0 mt-0.5" />
                      )}
                      <div className="flex-1">
                        <AlertTitle className="font-mono text-[10px] uppercase tracking-widest">
                          {message.type === "error"
                            ? "Ingestion Error"
                            : "Indexed Successfully"}
                        </AlertTitle>
                        <AlertDescription className="font-sans text-sm mt-1">
                          {message.text}
                        </AlertDescription>
                      </div>
                      <button
                        onClick={() => setMessage(null)}
                        className="shrink-0 opacity-50 hover:opacity-100 transition-opacity"
                      >
                        <X className="w-3.5 h-3.5" />
                      </button>
                    </div>
                  </Alert>
                </div>
              )}
            </section>

            {/* Indexed Documents List */}
            {files.length > 0 && (
              <section className="animate-in fade-in duration-300">
                <div className="flex items-center justify-between mb-4">
                  <h2 className="font-mono text-[10px] uppercase tracking-widest text-muted-foreground flex items-center gap-2">
                    <div className="w-1.5 h-1.5 rounded-full bg-[#00C4A0]" />
                    Indexed Documents
                  </h2>
                  <Button
                    variant="ghost"
                    size="sm"
                    className="h-7 text-[10px] font-mono uppercase tracking-wider text-destructive/70 hover:text-destructive hover:bg-destructive/10 rounded-sm"
                    onClick={handleClearAll}
                  >
                    <Trash2 className="w-3 h-3 mr-1.5" />
                    Clear All
                  </Button>
                </div>

                <div className="space-y-2">
                  {files.map((file) => {
                    const isDeleting = deletingId === file.id;
                    const status = file.status || "ready";
                    return (
                      <div
                        key={file.id}
                        className={`
                                                    group flex items-center gap-3 p-3.5 rounded-sm border transition-all
                                                    ${isDeleting
                            ? "opacity-40 scale-[0.98] border-border/30"
                            : "border-border/50 bg-card hover:border-[#00C4A0]/20 hover:bg-card/80"
                          }
                                                `}
                      >
                        {/* File Icon */}
                        <div className="w-9 h-9 rounded-sm border border-border/50 bg-muted/50 flex items-center justify-center shrink-0">
                          {getFileIcon(file.type, file.name)}
                        </div>

                        {/* File Info */}
                        <div className="flex-1 min-w-0">
                          <p className="font-sans text-sm text-foreground truncate">
                            {file.name}
                          </p>
                          <div className="flex items-center gap-3 mt-0.5">
                            <span className="font-mono text-[10px] text-muted-foreground uppercase">
                              {formatFileSize(file.size)}
                            </span>
                            <span className="font-mono text-[10px] text-muted-foreground">
                              {formatDate(file.createdAt)}
                            </span>
                            <span
                              className={`font-mono text-[10px] uppercase px-1.5 py-0.5 rounded-sm ${
                                status === "ready"
                                  ? "bg-[#00C4A0]/15 text-[#00C4A0]"
                                  : status === "failed"
                                    ? "bg-destructive/10 text-destructive"
                                    : "bg-muted text-muted-foreground"
                              }`}
                            >
                              {status}
                            </span>
                          </div>
                          {status === "failed" && file.processingError && (
                            <p className="mt-1 text-[10px] font-mono text-destructive/80 truncate">
                              {file.processingError}
                            </p>
                          )}
                        </div>

                        {/* Delete Button */}
                        <Button
                          variant="ghost"
                          size="icon"
                          className="h-8 w-8 rounded-sm opacity-40 group-hover:opacity-100 transition-all hover:bg-destructive/10"
                          onClick={() => handleDeleteFile(file.id)}
                          disabled={isDeleting}
                          title="Remove from knowledge base"
                        >
                          {isDeleting ? (
                            <Loader2 className="w-3.5 h-3.5 animate-spin" />
                          ) : (
                            <Trash2 className="w-3.5 h-3.5 text-destructive" />
                          )}
                        </Button>
                      </div>
                    );
                  })}
                </div>
              </section>
            )}

            {/* Empty State — only show when no files AND no loading */}
            {files.length === 0 && !isLoading && (
              <section className="text-center py-10 opacity-40">
                <Database className="w-10 h-10 mx-auto mb-3 text-muted-foreground" />
                <p className="font-mono text-[10px] uppercase tracking-widest text-muted-foreground">
                  Knowledge base empty
                </p>
                <p className="font-sans text-xs text-muted-foreground mt-1">
                  Upload your first document to get started
                </p>
              </section>
            )}
          </div>
        </div>
      </div>
    </ThreePaneLayout>
  );
}
