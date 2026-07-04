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
  RefreshCw,
} from "lucide-react";
import { ThreePaneLayout } from "@/components/three-pane-layout";
import { useSourceUpload } from "@/hooks/useSourceUpload";

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

function getStatusCopy(status: string) {
  switch (status) {
    case "uploading":
      return {
        label: "Uploading",
        tone: "bg-muted text-muted-foreground",
        description: "Waiting for the private upload to complete.",
      };
    case "queued":
      return {
        label: "Queued",
        tone: "bg-muted text-muted-foreground",
        description: "Ready for indexing. You can start it now.",
      };
    case "processing":
      return {
        label: "Indexing",
        tone: "bg-rag/15 text-rag",
        description: "Extracting text, chunking, and embedding.",
      };
    case "retrying":
      return {
        label: "Retrying",
        tone: "bg-accent/15 text-accent",
        description: "A previous attempt failed. Scribe will try again.",
      };
    case "failed":
      return {
        label: "Failed",
        tone: "bg-destructive/10 text-destructive",
        description: "Indexing stopped. Check the error below.",
      };
    default:
      return {
        label: "Ready",
        tone: "bg-rag/15 text-rag",
        description: "Available for retrieval and citation.",
      };
  }
}

export default function DocumentUpload() {
  const [isDragging, setIsDragging] = useState(false);
  const [message, setMessage] = useState<{
    type: "error" | "success";
    text: string;
  } | null>(null);
  const [files, setFiles] = useState<any[]>([]);
  const [deletingId, setDeletingId] = useState<number | null>(null);
  const [processingNowId, setProcessingNowId] = useState<number | null>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const {
    state: sourceUpload,
    uploadSource,
    processSourceNow,
    reset: resetUpload,
  } = useSourceUpload();
  const isUploading = sourceUpload.status === "reserving" || sourceUpload.status === "uploading";
  const hasPendingIngestion = isUploading || files.some((file) =>
    ["uploading", "queued", "processing", "retrying"].includes(file.status)
  );

  const fetchFiles = useCallback(async () => {
    const data = await getFiles();
    setFiles(data);
  }, []);

  useEffect(() => {
    fetchFiles();
  }, [fetchFiles]);

  useEffect(() => {
    const hasPending = files.some((file) =>
      ["uploading", "queued", "processing", "retrying"].includes(file.status)
    );
    if (!hasPending) return;

    const interval = setInterval(() => {
      fetchFiles().catch(() => {
        // noop
      });
    }, 5000);

    return () => clearInterval(interval);
  }, [files, fetchFiles]);

  const processFile = async (file: File) => {
    setMessage(null);
    resetUpload();
    const result = await uploadSource(file);
    if (result.success) {
      setMessage({
        type: "success",
        text:
          result.status === "ready"
            ? "Source indexed and ready to cite."
            : result.status === "processing"
              ? "Source uploaded securely. Indexing has started."
              : "Source uploaded securely and queued for indexing.",
      });
      if (fileInputRef.current) fileInputRef.current.value = "";
      await fetchFiles();
    } else {
      setMessage({ type: "error", text: result.error });
      await fetchFiles();
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

  const handleProcessNow = async (id: number) => {
    setProcessingNowId(id);
    setMessage(null);
    try {
      const result = await processSourceNow(id);
      await fetchFiles();
      setMessage({
        type: "success",
        text:
          (result.ready ?? 0) > 0
            ? "Source indexed and ready to cite."
            : (result.claimed ?? 0) > 0
              ? "Indexing started. The Source list will refresh while it finishes."
              : "No queued work was ready yet. Scribe will keep checking.",
      });
    } catch (error) {
      setMessage({
        type: "error",
        text: error instanceof Error ? error.message : "Could not start indexing",
      });
    } finally {
      setProcessingNowId(null);
    }
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
    <ThreePaneLayout status={hasPendingIngestion ? "submitted" : "ready"}>
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
            <section>
              <div className="text-center mb-8">
                <h1 className="font-serif text-3xl text-foreground mb-2 tracking-tight text-balance">
                  Sources
                </h1>
                <p className="font-sans text-sm text-muted-foreground max-w-md mx-auto">
                  Add documents, index them, and make every answer traceable to evidence.
                </p>
              </div>

              <div
                onDragOver={(e) => {
                  e.preventDefault();
                  setIsDragging(true);
                }}
                onDragLeave={() => setIsDragging(false)}
                onDrop={handleDrop}
                onClick={() => !isUploading && fileInputRef.current?.click()}
                onKeyDown={(event) => {
                  if (!isUploading && (event.key === "Enter" || event.key === " ")) {
                    event.preventDefault();
                    fileInputRef.current?.click();
                  }
                }}
                role="button"
                tabIndex={isUploading ? -1 : 0}
                aria-disabled={isUploading}
                aria-label="Upload a Source document"
                className={`
                                    relative border-2 border-dashed rounded-md p-8 sm:p-10 text-center cursor-pointer transition-all duration-200
                                    ${isDragging
                    ? "border-rag bg-rag/5"
                    : "border-border/60 hover:border-rag/40 hover:bg-muted/30"
                  }
                                    ${isUploading ? "pointer-events-none opacity-60" : ""}
                                `}
              >
                <input
                  ref={fileInputRef}
                  type="file"
                  accept=".pdf,.txt,.md,.csv,.docx"
                  onChange={handleFileUpload}
                  onClick={(e) => e.stopPropagation()}
                  disabled={isUploading}
                  className="hidden"
                />

                {isUploading ? (
                  <div className="flex flex-col items-center gap-4 py-4">
                    <Loader2 className="w-8 h-8 animate-spin text-rag" />
                    <div>
                      <p className="font-mono text-xs uppercase tracking-wider text-foreground">
                        {sourceUpload.status === "reserving"
                          ? "Reserving private upload"
                          : `Uploading Source, ${sourceUpload.progress}%`}
                      </p>
                      <p className="font-mono text-[10px] text-muted-foreground mt-1">
                        Private upload, queue, extract, embed
                      </p>
                    </div>
                    <div
                      className="w-48 h-1 bg-muted rounded-full overflow-hidden"
                      role="progressbar"
                      aria-valuemin={0}
                      aria-valuemax={100}
                      aria-valuenow={
                        sourceUpload.status === "reserving" ? 20 : sourceUpload.progress
                      }
                    >
                      <div
                        className={`h-full bg-rag rounded-full transition-all duration-500 ${sourceUpload.status === "reserving" ? "animate-pulse" : ""}`}
                        style={{
                          width: sourceUpload.status === "reserving"
                            ? "20%"
                            : `${sourceUpload.progress}%`,
                        }}
                      />
                    </div>
                  </div>
                ) : (
                  <div className="flex flex-col items-center gap-3 py-4">
                    <div
                      className={`
                        w-14 h-14 rounded-full border-2 flex items-center justify-center transition-all
                        ${isDragging ? "border-rag bg-rag/10" : "border-border bg-card"}
                      `}
                    >
                      <UploadCloud
                        className={`w-6 h-6 transition-colors ${isDragging ? "text-rag" : "text-muted-foreground"}`}
                      />
                    </div>
                    <div>
                      <p className="font-sans text-sm text-foreground">
                        <span className="text-rag font-medium">
                          Click to upload
                        </span>{" "}
                        or drag and drop
                      </p>
                      <p className="font-mono text-[10px] text-muted-foreground mt-1.5 uppercase tracking-wider">
                        PDF • TXT • MD • CSV • DOCX
                      </p>
                      <p className="text-[11px] text-muted-foreground mt-3 max-w-sm mx-auto bg-muted/50 p-2 rounded border border-border/50">
                        <strong className="text-foreground">Private by default:</strong> Originals upload to secure storage, then index in the background.
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
                    className={`rounded-sm border relative ${message.type === "success" ? "border-rag/30 bg-rag/5" : ""}`}
                  >
                    <div className="flex items-start gap-2 w-full">
                      {message.type === "success" ? (
                        <CheckCircle2 className="w-4 h-4 text-rag shrink-0 mt-0.5" />
                      ) : (
                        <AlertCircle className="w-4 h-4 shrink-0 mt-0.5" />
                      )}
                      <div className="flex-1 min-w-0">
                        <AlertTitle className="font-mono text-[10px] uppercase tracking-widest">
                          {message.type === "error"
                            ? "Source intake error"
                            : "Source queued"}
                        </AlertTitle>
                        <AlertDescription className="font-sans text-sm mt-1 break-words">
                          {message.text}
                        </AlertDescription>
                      </div>
                      <button
                        onClick={() => setMessage(null)}
                        className="shrink-0 opacity-50 hover:opacity-100 transition-opacity"
                        aria-label="Dismiss upload message"
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
                    <div className="w-1.5 h-1.5 rounded-full bg-rag" />
                    Source library
                  </h2>
                  <Button
                    variant="ghost"
                    size="sm"
                    className="h-7 text-[10px] font-mono uppercase tracking-wider text-destructive/70 hover:text-destructive hover:bg-destructive/10 rounded-sm"
                    onClick={handleClearAll}
                  >
                    <Trash2 className="w-3 h-3 mr-1.5" />
                    Clear all
                  </Button>
                </div>

                <div className="space-y-2">
                  {files.map((file) => {
                    const isDeleting = deletingId === file.id;
                    const status = file.status || "ready";
                    const statusCopy = getStatusCopy(status);
                    const canProcessNow = ["queued", "retrying"].includes(status);
                    return (
                      <div
                        key={file.id}
                        className={`
                                                    group flex items-center gap-3 p-3.5 rounded-sm border transition-all
                                                    ${isDeleting
                            ? "opacity-40 scale-[0.98] border-border/30"
                            : "border-border/50 bg-card hover:border-rag/20 hover:bg-card/80"
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
                              className={`font-mono text-[10px] uppercase px-1.5 py-0.5 rounded-sm ${statusCopy.tone}`}
                              title={statusCopy.description}
                            >
                              {statusCopy.label}
                            </span>
                          </div>
                          {status !== "ready" && status !== "failed" && (
                            <p className="mt-1 text-[11px] text-muted-foreground truncate">
                              {statusCopy.description}
                            </p>
                          )}
                          {status === "failed" && file.processingError && (
                            <p className="mt-1 text-[10px] font-mono text-destructive/80 truncate">
                              {file.processingError}
                            </p>
                          )}
                        </div>

                        {canProcessNow && (
                          <Button
                            variant="outline"
                            size="sm"
                            className="h-8 shrink-0 rounded-sm px-2 text-[10px] font-mono uppercase tracking-wider"
                            onClick={() => handleProcessNow(file.id)}
                            disabled={processingNowId === file.id || isDeleting}
                          >
                            {processingNowId === file.id ? (
                              <Loader2 className="mr-1.5 h-3 w-3 animate-spin" />
                            ) : (
                              <RefreshCw className="mr-1.5 h-3 w-3" />
                            )}
                            Process now
                          </Button>
                        )}

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

            {files.length === 0 && !isUploading && (
              <section className="text-center py-10 text-muted-foreground">
                <Database className="w-10 h-10 mx-auto mb-3 opacity-50" />
                <p className="font-mono text-[10px] uppercase tracking-widest text-muted-foreground">
                  No Sources yet
                </p>
                <p className="font-sans text-xs text-muted-foreground mt-1">
                  Upload a document to make the first cited answer possible.
                </p>
              </section>
            )}
          </div>
        </div>
      </div>
    </ThreePaneLayout>
  );
}
