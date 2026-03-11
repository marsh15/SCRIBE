"use client";

import { ThreePaneLayout } from "@/components/three-pane-layout";
import { ChatProvider, useChatState } from "@/components/chat-context";
import { ReactNode } from "react";

function ChatLayoutInner({ children }: { children: ReactNode }) {
    const { messages, status } = useChatState();
    return (
        <ThreePaneLayout messages={messages} status={status}>
            {children}
        </ThreePaneLayout>
    );
}

export default function ChatLayout({ children }: { children: ReactNode }) {
    return (
        <ChatProvider>
            <ChatLayoutInner>{children}</ChatLayoutInner>
        </ChatProvider>
    );
}
