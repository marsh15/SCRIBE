"use server";

import { db } from "@/lib/db-config";
import { chats, chatMessages } from "@/lib/db-schema";
import { desc, eq, and } from "drizzle-orm";
import { revalidatePath } from "next/cache";
import { nanoid } from "nanoid";
import { resolveWorkspace } from "@/lib/workspace";

export async function getChats() {
    try {
        const { userId, workspaceId } = await resolveWorkspace();
        return await db.select().from(chats).where(and(eq(chats.userId, userId), eq(chats.workspaceId, workspaceId))).orderBy(desc(chats.createdAt));
    } catch (err) {
        console.error("Failed to fetch chats:", err);
        return [];
    }
}

export async function getChatMessages(chatId: string) {
    try {
        const { userId, workspaceId } = await resolveWorkspace();
        return await db
            .select({
                id: chatMessages.id,
                chatId: chatMessages.chatId,
                role: chatMessages.role,
                content: chatMessages.content,
                parts: chatMessages.parts,
                createdAt: chatMessages.createdAt,
            })
            .from(chatMessages)
            .innerJoin(chats, eq(chatMessages.chatId, chats.id))
            .where(and(eq(chatMessages.chatId, chatId), eq(chats.userId, userId), eq(chats.workspaceId, workspaceId)))
            .orderBy(chatMessages.createdAt);
    } catch (err) {
        console.error("Failed to fetch chat messages:", err);
        return [];
    }
}

export async function createChat(title: string) {
    try {
        const { userId, workspaceId } = await resolveWorkspace();
        const newChatId = nanoid();
        const newChat = await db
            .insert(chats)
            .values({
                id: newChatId,
                title,
                userId,
                workspaceId,
            })
            .returning();

        revalidatePath("/");
        revalidatePath("/chat");
        return newChat[0];
    } catch (err) {
        console.error("Failed to create chat:", err);
        throw new Error("Failed to create chat");
    }
}

export async function deleteChat(id: string) {
    try {
        const { userId, workspaceId } = await resolveWorkspace();
        const chat = await db.query.chats.findFirst({
            where: and(eq(chats.id, id), eq(chats.userId, userId), eq(chats.workspaceId, workspaceId)),
            columns: { id: true },
        });

        if (!chat) {
            return false;
        }

        await db.delete(chatMessages).where(eq(chatMessages.chatId, id));
        await db.delete(chats).where(and(eq(chats.id, id), eq(chats.userId, userId), eq(chats.workspaceId, workspaceId)));
        revalidatePath("/");
        revalidatePath("/chat");
        return true;
    } catch (err) {
        console.error("Failed to delete chat:", err);
        return false;
    }
}
