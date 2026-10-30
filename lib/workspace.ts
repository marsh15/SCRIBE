import { sql } from "drizzle-orm";
import { db } from "@/lib/db-config";
import { getUserId } from "@/lib/auth";

export interface WorkspaceContext {
  userId: string;
  workspaceId: string;
}

/**
 * Resolve or create a personal workspace through the narrowly privileged database
 * function. The caller must supply an authenticated server-derived user ID.
 * Migration 0013 must be applied before this function is used.
 */
export async function ensurePersonalWorkspace(userId: string): Promise<string> {
  if (!userId) throw new Error("ensurePersonalWorkspace requires a userId");

  const rows = await db.select({ id: sql<string>`public.app_ensure_personal_workspace(${userId})` }).from(sql`(select 1) as workspace_request`);
  const workspaceId = rows[0]?.id;
  if (!workspaceId) throw new Error("Failed to resolve personal workspace");
  return workspaceId;
}

/** Resolve workspace identity from the authenticated user, never client input. */
export async function resolveWorkspace(): Promise<WorkspaceContext> {
  const userId = await getUserId();
  const workspaceId = await ensurePersonalWorkspace(userId);
  return { userId, workspaceId };
}
