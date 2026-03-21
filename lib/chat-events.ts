export const CHAT_CREATED_EVENT = "scribe:chat-created";
export const SIDEBAR_REFRESH_EVENT = "scribe:sidebar-refresh";

export type SidebarChatSummary = {
  id: string;
  title: string;
};

export function notifyChatCreated(chat: SidebarChatSummary) {
  if (typeof window === "undefined") return;

  window.dispatchEvent(
    new CustomEvent<SidebarChatSummary>(CHAT_CREATED_EVENT, {
      detail: chat,
    }),
  );
}

export function notifySidebarRefresh() {
  if (typeof window === "undefined") return;
  window.dispatchEvent(new Event(SIDEBAR_REFRESH_EVENT));
}
