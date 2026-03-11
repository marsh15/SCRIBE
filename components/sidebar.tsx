"use client";

import { useEffect, useState, useCallback, useRef } from "react";
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
import { UserButton, SignedIn, SignedOut, SignInButton, useUser } from "@clerk/nextjs";
import { Group, Panel, Separator } from "react-resizable-panels";
import { ThemeToggle } from "@/components/theme-toggle";

function ConfirmDeleteButton({
  onConfirm,
  disabled,
  title,
  className
}: {
  onConfirm: () => void;
  disabled?: boolean;
  title?: string;
  className?: string;
}) {
  const [isConfirming, setIsConfirming] = useState(false);
  const timeoutRef = useRef<NodeJS.Timeout | null>(null);

  useEffect(() => {
    return () => {
      if (timeoutRef.current) clearTimeout(timeoutRef.current);
    };
  }, []);

  const handleClick = (e: React.MouseEvent) => {
    e.preventDefault();
    e.stopPropagation();

    if (disabled) return;

    if (isConfirming) {
      if (timeoutRef.current) clearTimeout(timeoutRef.current);
      setIsConfirming(false);
      onConfirm();
    } else {
      setIsConfirming(true);
      timeoutRef.current = setTimeout(() => {
        setIsConfirming(false);
      }, 3000);
    }
  };

  return (
    <Button
      variant="ghost"
      size={isConfirming ? "sm" : "icon"}
      className={className || `h-6 ${isConfirming ? 'w-auto px-2 bg-destructive/10 text-destructive hover:bg-destructive/20 border border-destructive/20' : 'w-6 opacity-90 hover:opacity-100 hover:bg-destructive/10'} transition-all shrink-0 rounded-sm`}
      onClick={handleClick}
      disabled={disabled}
      title={isConfirming ? "Click again to confirm" : title}
    >
      {isConfirming ? (
        <span className="text-[10px] font-mono uppercase tracking-wider">Confirm</span>
      ) : (
        <Trash2 className="h-3 w-3 text-destructive" />
      )}
    </Button>
  );
}

type SidebarFile = {
  id: number;
  name: string;
  type: string;
  status?: string;
};

type SidebarChat = {
  id: string;
  title: string;
};

function getFileIcon(type: string, name: string) {
  const ext = name.split(".").pop()?.toLowerCase();
  if (type === "application/pdf" || ext === "pdf") {
    return <FileText className="w-4 h-4" />;
  }
  if (type === "text/csv" || ext === "csv") {
    return <FileSpreadsheet className="w-4 h-4" />;
  }
  if (ext === "zip" || ext === "rar") {
    return <FileArchive className="w-4 h-4" />;
  }
  return <File className="w-4 h-4" />;
}

export function Sidebar() {
  const { user } = useUser();
  const [files, setFiles] = useState<SidebarFile[]>([]);
  const [chats, setChats] = useState<SidebarChat[]>([]);
  const [loading, setLoading] = useState(true);
  const [deletingChatId, setDeletingChatId] = useState<string | null>(null);
  const [deletingFileId, setDeletingFileId] = useState<number | null>(null);
  const [refreshing, setRefreshing] = useState(false);
  const pathname = usePathname();
  const router = useRouter();

  const activeChatId = pathname.startsWith("/chat/")
    ? pathname.replace("/chat/", "")
    : null;

  const fetchData = useCallback(async () => {
    try {
      const [filesData, chatsData] = await Promise.all([getFiles(), getChats()]);
      setFiles(filesData as SidebarFile[]);
      setChats(chatsData as SidebarChat[]);
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
    setTimeout(() => setRefreshing(false), 400);
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
      <div className="p-4 pb-0">
        <div className="mb-4 flex items-center justify-between">
          <div className="flex items-center gap-2">
            <div className="w-7 h-7 rounded-sm bg-primary flex items-center justify-center">
              <BookOpen className="w-3.5 h-3.5 text-primary-foreground" />
            </div>
            <h1 className="font-serif text-xl font-normal tracking-tight">Scribe</h1>
          </div>
          <div className="flex items-center gap-0.5">
            <ThemeToggle />
            <Button
              variant="ghost"
              size="icon"
              className={`h-7 w-7 rounded-sm opacity-60 hover:opacity-100 transition-all ${refreshing ? "animate-spin" : ""}`}
              onClick={handleRefresh}
              title="Refresh"
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
        <div className="mb-3 grid grid-cols-2 gap-2">
          <Link
            href="/settings/billing"
            className="rounded-sm border border-border px-2 py-1.5 text-[10px] font-mono uppercase tracking-wider text-muted-foreground hover:text-foreground hover:bg-muted"
          >
            Billing
          </Link>
          <Link
            href="/changelog"
            className="rounded-sm border border-border px-2 py-1.5 text-[10px] font-mono uppercase tracking-wider text-muted-foreground hover:text-foreground hover:bg-muted"
          >
            Changelog
          </Link>
        </div>
      </div>

      <Group orientation="vertical" id="sidebar-vertical-group" className="flex-1 w-full min-h-0">
        <Panel defaultSize={60} minSize={30} id="sidebar-chats" className="flex flex-col px-4 min-h-0">
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
                  <div key={i} className="h-8 bg-muted/50 rounded-sm animate-pulse" />
                ))}
              </div>
            ) : chats.length === 0 ? (
              <div className="p-4 text-center">
                <MessageSquare className="w-8 h-8 text-muted-foreground/30 mx-auto mb-2" />
                <p className="text-xs font-sans text-muted-foreground">No conversations yet</p>
                <p className="text-[10px] font-mono text-muted-foreground/60 mt-1">Start a new chat above</p>
              </div>
            ) : (
              <div className="space-y-1 p-2">
                {chats.map((chat) => {
                  const isActive = activeChatId === chat.id;
                  const isDeleting = deletingChatId === chat.id;

                  return (
                    <div
                      key={chat.id}
                      className={`group flex w-full items-center justify-between p-2 text-sm rounded-sm transition-all duration-200 overflow-hidden border ${isActive ? "bg-primary/5 border-border/80" : "border-transparent hover:bg-muted"
                        } ${isDeleting ? "opacity-50 scale-95" : ""}`}
                    >
                      <Link
                        href={`/chat/${chat.id}`}
                        className="flex-1 min-w-0 flex items-center gap-2 overflow-hidden mr-2 group/link"
                      >
                        <MessageSquare
                          className={`w-3.5 h-3.5 shrink-0 transition-transform group-hover/link:scale-110 ${isActive ? "text-[#00C4A0]" : "text-muted-foreground"}`}
                        />
                        <span className={`truncate font-sans text-sm transition-colors group-hover/link:text-foreground ${isActive ? "font-medium" : ""}`}>
                          {chat.title || "Untitled Chat"}
                        </span>
                      </Link>

                      <ConfirmDeleteButton
                        onConfirm={() => handleDeleteChat(chat.id)}
                        disabled={isDeleting}
                        title="Delete chat"
                      />
                    </div>
                  );
                })}
              </div>
            )}
          </ScrollArea>
        </Panel>

        <Separator className="h-[3px] w-full bg-border hover:bg-[#00C4A0]/50 active:bg-[#00C4A0] transition-colors cursor-row-resize relative my-2" />

        <Panel defaultSize={40} minSize={25} id="sidebar-kb" className="flex flex-col min-h-0 px-4 pb-2">
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
                  className="h-5 w-5 rounded-sm opacity-60 hover:opacity-100 hover:bg-destructive/10 transition-all"
                  title="Clear all files"
                  onClick={async () => {
                    const ok = confirm(
                      "Delete ALL files from your knowledge base? This cannot be undone."
                    );
                    if (!ok) return;
                    for (const file of files) {
                      await deleteFile(file.id);
                    }
                    await fetchData();
                  }}
                >
                  <Trash2 className="h-3 w-3 text-destructive" />
                </Button>
              )}
              <Link href="/upload">
                <Button
                  variant="ghost"
                  size="icon"
                  className="h-5 w-5 rounded-sm opacity-60 hover:opacity-100 hover:text-[#00C4A0] transition-all"
                  title="Upload"
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
                  <div key={i} className="h-8 bg-muted/50 rounded-sm animate-pulse" />
                ))}
              </div>
            ) : files.length === 0 ? (
              <div className="p-3 text-center">
                <p className="text-xs font-sans text-muted-foreground">No documents indexed</p>
              </div>
            ) : (
              <div className="space-y-1 p-2">
                {files.map((file) => {
                  const isDeleting = deletingFileId === file.id;
                  const status = file.status || "ready";

                  return (
                    <div
                      key={file.id}
                      className={`group flex w-full items-center justify-between p-2 text-sm rounded-sm hover:bg-muted transition-all border border-transparent overflow-hidden ${isDeleting ? "opacity-50 scale-95" : ""
                        }`}
                    >
                      <Link
                        href={`/files/${file.id}`}
                        className="flex-1 min-w-0 flex items-center gap-2 overflow-hidden mr-2 group/link"
                        title="View document"
                      >
                        <span className="text-muted-foreground shrink-0 group-hover/link:text-[#00C4A0] transition-colors group-hover/link:scale-110">
                          {getFileIcon(file.type, file.name)}
                        </span>
                        <span className="truncate font-sans text-foreground/80 text-sm group-hover/link:text-foreground transition-colors">
                          {file.name}
                        </span>
                        <span
                          className={`text-[9px] font-mono uppercase px-1 rounded-sm shrink-0 ${status === "ready"
                            ? "bg-[#00C4A0]/15 text-[#00C4A0]"
                            : status === "failed"
                              ? "bg-destructive/15 text-destructive"
                              : "bg-muted text-muted-foreground"
                            }`}
                        >
                          {status}
                        </span>
                        <Eye className="w-3 h-3 text-muted-foreground/0 group-hover/link:text-muted-foreground/60 transition-all shrink-0" />
                      </Link>

                      <ConfirmDeleteButton
                        onConfirm={() => handleDeleteFile(file.id)}
                        disabled={isDeleting}
                        title="Delete file"
                      />
                    </div>
                  );
                })}
              </div>
            )}
          </ScrollArea>
        </Panel>
      </Group>

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
              <p className="font-mono text-[10px] uppercase tracking-widest text-muted-foreground truncate">
                {user?.fullName || user?.primaryEmailAddress?.emailAddress || "Account"}
              </p>
              {user?.fullName && (
                <p className="font-sans text-[10px] text-muted-foreground/60 truncate">
                  {user.primaryEmailAddress?.emailAddress}
                </p>
              )}
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
