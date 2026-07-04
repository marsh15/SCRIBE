"use client";

import { ThreePaneLayout } from "@/components/three-pane-layout";
import { ChatProvider, useChatState } from "@/components/chat-context";
import { ReactNode } from "react";
import { useParams } from "next/navigation";

function ChatLayoutInner({ children }: { children: ReactNode }) {
    const { messages, status } = useChatState();
    const params = useParams();
    const chatId = typeof params.id === "string" ? params.id : undefined;

    return (
        <ThreePaneLayout messages={messages} status={status} chatId={chatId}>
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
