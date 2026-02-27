"use client";

import { useEffect, useState, useCallback } from "react";
import { usePathname, useRouter } from "next/navigation";
import { getFiles, deleteFile } from "@/app/files/actions";
import { getChats, deleteChat } from "@/app/chat/actions";
import {
  Trash2,
  FileText,
  File,
  FileSpreadsheet,
  FileArchive,
  Plus,
  MessageSquarePlus,
  MessageSquare,
  BookOpen,
  RefreshCw,
  LogOut,
  Eye,
} from "lucide-react";
import { ScrollArea } from "@/components/ui/scroll-area";
import { Button } from "@/components/ui/button";
import Link from "next/link";
import { UserButton, SignedIn, SignedOut, SignInButton } from "@clerk/nextjs";
import { Group, Panel, Separator } from "react-resizable-panels";
import { ThemeToggle } from "@/components/theme-toggle";

function getFileIcon(type: string, name: string) {
  const ext = name.split(".").pop()?.toLowerCase();
  if (type === "application/pdf" || ext === "pdf")
    return <FileText className="w-4 h-4" />;
  if (type === "text/csv" || ext === "csv")
    return <FileSpreadsheet className="w-4 h-4" />;
  if (ext === "zip" || ext === "rar")
    return <FileArchive className="w-4 h-4" />;
  return <File className="w-4 h-4" />;
}

export function Sidebar() {
  const [files, setFiles] = useState<any[]>([]);
  const [chats, setChats] = useState<any[]>([]);
  const [loading, setLoading] = useState(true);
  const [deletingChatId, setDeletingChatId] = useState<string | null>(null);
  const [deletingFileId, setDeletingFileId] = useState<number | null>(null);
  const [confirmDeleteId, setConfirmDeleteId] = useState<string | number | null>(null);
  const [refreshing, setRefreshing] = useState(false);
  const pathname = usePathname();
  const router = useRouter();

  const activeChatId = pathname.startsWith("/chat/")
    ? pathname.replace("/chat/", "")
    : null;

  const fetchData = useCallback(async () => {
    try {
      const [filesData, chatsData] = await Promise.all([
        getFiles(),
        getChats(),
      ]);
      setFiles(filesData);
      setChats(chatsData);
    } catch (err) {
      console.error("Failed to fetch sidebar data:", err);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchData();
  }, [pathname, fetchData]);

  const handleRefresh = async () => {
    setRefreshing(true);
    await fetchData();
    setTimeout(() => setRefreshing(false), 500);
  };

  async function handleDeleteFile(id: number) {
    setDeletingFileId(id);
    await deleteFile(id);
    await fetchData();
    setDeletingFileId(null);
  }

  async function handleDeleteChat(id: string) {
    setDeletingChatId(id);
    await deleteChat(id);
    await fetchData();
    setDeletingChatId(null);
    if (activeChatId === id) {
      router.push("/chat");
    }
  }

  return (
    <div className="flex flex-col h-full overflow-hidden">
      {/* Header */}
      <div className="p-4 pb-0">
        <div className="mb-4 flex items-center justify-between">
          <div className="flex items-center gap-2">
            <div className="w-7 h-7 rounded-sm bg-primary flex items-center justify-center">
              <BookOpen className="w-3.5 h-3.5 text-primary-foreground" />
            </div>
            <h1 className="font-serif text-xl font-normal tracking-tight">
              Scribe
            </h1>
          </div>
          <div className="flex items-center gap-0.5">
            <ThemeToggle />
            <Button
              variant="ghost"
              size="icon"
              className={`h-7 w-7 rounded-sm opacity-50 hover:opacity-100 transition-all ${refreshing ? "animate-spin" : ""}`}
              onClick={handleRefresh}
            >
              <RefreshCw className="h-3 w-3" />
            </Button>
          </div>
        </div>

        <Link href="/chat">
          <Button
            variant="outline"
            className="w-full justify-start gap-2 font-mono text-xs uppercase tracking-wider mb-4 bg-background border-border hover:bg-muted/50 hover:border-[#00C4A0]/30 transition-all h-10 rounded-sm group"
          >
            <MessageSquarePlus className="w-4 h-4 text-[#00C4A0] group-hover:scale-110 transition-transform" />
            New Chat
          </Button>
        </Link>
      </div>

      {/* Chat History Section */}
      <Group orientation="vertical" id="sidebar-vertical-group" className="flex-1 w-full min-h-0">
        <Panel defaultSize={60} id="sidebar-chats" className="flex flex-col px-4 min-h-0">
          <div className="flex items-center justify-between mb-2">
            <h2 className="font-mono text-[10px] uppercase tracking-widest text-muted-foreground">
              Recent Chats
            </h2>
            <span className="font-mono text-[10px] bg-muted px-1.5 py-0.5 rounded-sm text-muted-foreground">
              {chats.length}
            </span>
          </div>
          <ScrollArea className="flex-1 -mx-2">
            {loading ? (
              <div className="p-2 space-y-2">
                {[1, 2, 3].map((i) => (
                  <div
                    key={i}
                    className="h-8 bg-muted/50 rounded-sm animate-pulse"
                  />
                ))}
              </div>
            ) : chats.length === 0 ? (
              <div className="p-4 text-center">
                <MessageSquare className="w-8 h-8 text-muted-foreground/30 mx-auto mb-2" />
                <p className="text-xs font-sans text-muted-foreground">
                  No conversations yet
                </p>
                <p className="text-[10px] font-mono text-muted-foreground/60 mt-1">
                  Start a new chat above
                </p>
              </div>
            ) : (
              <div className="space-y-0.5 p-2">
                {chats.map((chat) => {
                  const isActive = activeChatId === chat.id;
                  const isDeleting = deletingChatId === chat.id;
                  const isConfirming = confirmDeleteId === chat.id;
                  return (
                    <div
                      key={chat.id}
                      onDoubleClick={(e) => {
                        e.preventDefault();
                        setConfirmDeleteId(chat.id);
                      }}
                      onMouseLeave={() => {
                        if (isConfirming) setConfirmDeleteId(null);
                      }}
                      className={`group flex w-full items-center justify-between p-2 text-sm rounded-sm transition-all duration-200 overflow-hidden ${isActive && !isConfirming
                        ? "bg-primary/5 border border-border/80"
                        : "hover:bg-muted border border-transparent"
                        } ${isDeleting ? "opacity-50 scale-95" : ""}`}
                    >
                      {isConfirming ? (
                        <Button
                          variant="ghost"
                          className="w-full h-6 text-xs text-destructive hover:bg-destructive hover:text-destructive-foreground transition-all"
                          onClick={(e) => {
                            e.preventDefault();
                            e.stopPropagation();
                            handleDeleteChat(chat.id);
                            setConfirmDeleteId(null);
                          }}
                          disabled={isDeleting}
                        >
                          <Trash2 className="h-3 w-3 mr-2 shrink-0" />
                          Delete Chat
                        </Button>
                      ) : (
                        <>
                          <Link
                            href={`/chat/${chat.id}`}
                            className="flex-1 min-w-0 flex items-center gap-2 overflow-hidden mr-2"
                          >
                            <MessageSquare
                              className={`w-3.5 h-3.5 shrink-0 transition-colors ${isActive ? "text-[#00C4A0]" : "text-muted-foreground"
                                }`}
                            />
                            <span
                              className={`truncate font-sans text-sm ${isActive
                                ? "text-foreground font-medium"
                                : "text-foreground/80"
                                }`}
                            >
                              {chat.title || "Untitled Chat"}
                            </span>
                          </Link>
                          <Button
                            variant="ghost"
                            size="icon"
                            className="h-6 w-6 opacity-0 group-hover:opacity-100 transition-all hover:bg-destructive/10 shrink-0 rounded-sm"
                            onClick={(e) => {
                              e.preventDefault();
                              e.stopPropagation();
                              handleDeleteChat(chat.id);
                            }}
                            disabled={isDeleting}
                          >
                            <Trash2 className="h-3 w-3 text-destructive" />
                          </Button>
                        </>
                      )}
                    </div>
                  );
                })}
              </div>
            )}
          </ScrollArea>
        </Panel>

        <Separator className="h-[3px] w-full bg-border hover:bg-[#00C4A0]/50 active:bg-[#00C4A0] transition-colors cursor-row-resize relative my-2">
          <div className="absolute top-1/2 left-1/2 -translate-x-1/2 -translate-y-1/2 opacity-0 hover:opacity-100 transition-opacity flex gap-[2px]">
            <div className="w-[3px] h-[3px] rounded-full bg-[#00C4A0]" />
            <div className="w-[3px] h-[3px] rounded-full bg-[#00C4A0]" />
            <div className="w-[3px] h-[3px] rounded-full bg-[#00C4A0]" />
          </div>
        </Separator>

        {/* Knowledge Base Section */}
        <Panel defaultSize={40} id="sidebar-kb" className="flex flex-col min-h-0 px-4 pb-2">
          <div className="flex items-center justify-between mb-2">
            <h2 className="font-mono text-[10px] uppercase tracking-widest text-muted-foreground">
              Knowledge Base
            </h2>
            <div className="flex items-center gap-1">
              <span className="font-mono text-[10px] bg-muted px-1.5 py-0.5 rounded-sm text-muted-foreground">
                {files.length}
              </span>
              {files.length > 0 && (
                <Button
                  variant="ghost"
                  size="icon"
                  className="h-5 w-5 rounded-sm opacity-50 hover:opacity-100 hover:bg-destructive/10 transition-all"
                  title="Clear all files"
                  onClick={async () => {
                    if (
                      !confirm(
                        "Delete ALL files from your knowledge base? This cannot be undone.",
                      )
                    )
                      return;
                    for (const file of files) {
                      await handleDeleteFile(file.id);
                    }
                  }}
                >
                  <Trash2 className="h-3 w-3 text-destructive" />
                </Button>
              )}
              <Link href="/upload">
                <Button
                  variant="ghost"
                  size="icon"
                  className="h-5 w-5 rounded-sm opacity-50 hover:opacity-100 hover:text-[#00C4A0] transition-all"
                >
                  <Plus className="h-3 w-3" />
                </Button>
              </Link>
            </div>
          </div>

          <ScrollArea className="flex-1 -mx-2">
            {loading ? (
              <div className="p-2 space-y-2">
                {[1, 2].map((i) => (
                  <div
                    key={i}
                    className="h-8 bg-muted/50 rounded-sm animate-pulse"
                  />
                ))}
              </div>
            ) : files.length === 0 ? (
              <div className="p-3 text-center">
                <p className="text-xs font-sans text-muted-foreground">
                  No documents indexed
                </p>
              </div>
            ) : (
              <div className="space-y-0.5 p-2">
                {files.map((file) => {
                  const isDeleting = deletingFileId === file.id;
                  const isConfirming = confirmDeleteId === file.id;
                  return (
                    <div
                      key={file.id}
                      onDoubleClick={(e) => {
                        e.preventDefault();
                        setConfirmDeleteId(file.id);
                      }}
                      onMouseLeave={() => {
                        if (isConfirming) setConfirmDeleteId(null);
                      }}
                      className={`group flex w-full items-center justify-between p-2 text-sm rounded-sm hover:bg-muted transition-all border border-transparent overflow-hidden ${isDeleting ? "opacity-50 scale-95" : ""
                        }`}
                    >
                      {isConfirming ? (
                        <Button
                          variant="ghost"
                          className="w-full h-6 text-xs text-destructive hover:bg-destructive hover:text-destructive-foreground transition-all"
                          onClick={(e) => {
                            e.preventDefault();
                            e.stopPropagation();
                            handleDeleteFile(file.id);
                            setConfirmDeleteId(null);
                          }}
                          disabled={isDeleting}
                        >
                          <Trash2 className="h-3 w-3 mr-2 shrink-0" />
                          Delete File
                        </Button>
                      ) : (
                        <>
                          <Link
                            href={`/files/${file.id}`}
                            className="flex-1 min-w-0 flex items-center gap-2 overflow-hidden mr-2 group/link"
                            title="View document chunks"
                          >
                            <span className="text-muted-foreground shrink-0 group-hover/link:text-[#00C4A0] transition-colors">
                              {getFileIcon(file.type, file.name)}
                            </span>
                            <span className="truncate font-sans text-foreground/80 text-sm group-hover/link:text-foreground transition-colors">
                              {file.name}
                            </span>
                            <Eye className="w-3 h-3 text-muted-foreground/0 group-hover/link:text-muted-foreground/60 transition-all shrink-0 ml-auto" />
                          </Link>
                          <Button
                            variant="ghost"
                            size="icon"
                            className="h-6 w-6 opacity-40 hover:opacity-100 transition-all hover:bg-destructive/10 shrink-0 rounded-sm"
                            onClick={() => handleDeleteFile(file.id)}
                            disabled={isDeleting}
                          >
                            <Trash2 className="h-3 w-3 text-destructive" />
                          </Button>
                        </>
                      )}
                    </div>
                  );
                })}
              </div>
            )}
          </ScrollArea>
        </Panel>
      </Group>

      {/* User Profile & Sign Out */}
      <div className="p-4 border-t border-border shrink-0">
        <SignedIn>
          <div className="flex items-center gap-3">
            <UserButton
              appearance={{
                elements: {
                  avatarBox: "w-8 h-8",
                },
              }}
            />
            <div className="flex-1 min-w-0">
              <p className="font-mono text-[10px] uppercase tracking-widest text-muted-foreground">
                Account
              </p>
            </div>
          </div>
        </SignedIn>
        <SignedOut>
          <SignInButton>
            <Button
              variant="outline"
              className="w-full justify-start gap-2 font-mono text-xs uppercase tracking-wider h-9 rounded-sm"
            >
              <LogOut className="w-3.5 h-3.5 rotate-180" />
              Sign In
            </Button>
          </SignInButton>
        </SignedOut>
      </div>
    </div>
  );
}
