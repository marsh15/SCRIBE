"use server";

import { db } from "@/lib/db-config";
import { scoped } from "@/lib/scoped";
import { chats, chatMessages } from "@/lib/db-schema";
import { desc, eq, and } from "drizzle-orm";
import { revalidatePath } from "next/cache";
import { nanoid } from "nanoid";
import { resolveWorkspace } from "@/lib/workspace";
import { readStoredVerifications, type VerificationData } from "@/lib/evidence/verification";

export async function getChats() {
    try {
        const { userId, workspaceId } = await resolveWorkspace();
        const [rows] = await scoped(workspaceId).batch([db.select().from(chats).where(and(eq(chats.userId, userId), eq(chats.workspaceId, workspaceId))).orderBy(desc(chats.createdAt))]);
        return rows;
    } catch (err) {
        console.error("Failed to fetch chats:", err);
        return [];
    }
}

export async function getChatMessages(chatId: string) {
    try {
        const { userId, workspaceId } = await resolveWorkspace();
        const [rows] = await scoped(workspaceId).batch([db
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
            .orderBy(chatMessages.createdAt)]);
        return rows;
    } catch (err) {
        console.error("Failed to fetch chat messages:", err);
        return [];
    }
}

export async function createChat(title: string) {
    try {
        const { userId, workspaceId } = await resolveWorkspace();
        const newChatId = nanoid();
        const [newChat] = await scoped(workspaceId).batch([db
            .insert(chats)
            .values({
                id: newChatId,
                title,
                userId,
                workspaceId,
            })
            .returning()]);

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
        const [deleted] = await scoped(workspaceId).batch([
            db.delete(chats).where(and(eq(chats.id, id), eq(chats.userId, userId), eq(chats.workspaceId, workspaceId))).returning({ id: chats.id }),
        ]);
        if (!deleted.length) return false;
        revalidatePath("/");
        revalidatePath("/chat");
        return true;
    } catch (err) {
        console.error("Failed to delete chat:", err);
        return false;
    }
}

/** Stored claim verifications (T26) for a chat's assistant messages. Empty on any failure: no badges, never a false one. */
export async function getChatVerifications(chatId: string): Promise<Record<string, VerificationData>> {
    try {
        const { userId, workspaceId } = await resolveWorkspace();
        const rows = await getChatMessages(chatId);
        const messageIds = rows.filter((m) => m.role === "assistant").map((m) => m.id);
        return await readStoredVerifications({ userId, workspaceId, messageIds });
    } catch (err) {
        console.error("Failed to fetch verifications:", err);
        return {};
    }
}
