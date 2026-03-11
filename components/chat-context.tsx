"use client";

import { createContext, useContext, useState, ReactNode } from "react";
import type { UIMessage } from "@ai-sdk/react";

type ChatContextType = {
    messages: UIMessage[];
    setMessages: (m: UIMessage[]) => void;
    status: string;
    setStatus: (s: string) => void;
};

export const ChatContext = createContext<ChatContextType | null>(null);

export function useChatState() {
    return useContext(ChatContext)!;
}

export function ChatProvider({ children }: { children: ReactNode }) {
    const [messages, setMessages] = useState<UIMessage[]>([]);
    const [status, setStatus] = useState("ready");

    return (
        <ChatContext.Provider value={{ messages, setMessages, status, setStatus }}>
            {children}
        </ChatContext.Provider>
    );
}
