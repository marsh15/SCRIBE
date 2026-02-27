"use server";

import { db } from "@/lib/db-config";
import { chats, chatMessages } from "@/lib/db-schema";
import { desc, eq, and } from "drizzle-orm";
import { revalidatePath } from "next/cache";
import { nanoid } from "nanoid";
import { getUserId } from "@/lib/auth";

export async function getChats() {
    try {
        const userId = await getUserId();
        return await db.select().from(chats).where(eq(chats.userId, userId)).orderBy(desc(chats.createdAt));
    } catch (err) {
        console.error("Failed to fetch chats:", err);
        return [];
    }
}

export async function getChatMessages(chatId: string) {
    try {
        return await db
            .select()
            .from(chatMessages)
            .where(eq(chatMessages.chatId, chatId))
            .orderBy(chatMessages.createdAt);
    } catch (err) {
        console.error("Failed to fetch chat messages:", err);
        return [];
    }
}

export async function createChat(title: string) {
    try {
        const userId = await getUserId();
        const newChatId = nanoid();
        const newChat = await db
            .insert(chats)
            .values({
                id: newChatId,
                title,
                userId,
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
        const userId = await getUserId();
        // Only allow deleting own chats
        await db.delete(chatMessages).where(eq(chatMessages.chatId, id));
        await db.delete(chats).where(and(eq(chats.id, id), eq(chats.userId, userId)));
        revalidatePath("/");
        revalidatePath("/chat");
        return true;
    } catch (err) {
        console.error("Failed to delete chat:", err);
        return false;
    }
}
