import { and, eq } from "drizzle-orm";
import { db } from "@/lib/db-config";
import { workspaceMembers, workspaces } from "@/lib/db-schema";
import { getUserId } from "@/lib/auth";

export interface WorkspaceContext {
  userId: string;
  workspaceId: string;
}

async function findPersonalWorkspaceId(userId: string): Promise<string | null> {
  const rows = await db
    .select({ id: workspaces.id })
    .from(workspaces)
    .innerJoin(
      workspaceMembers,
      and(
        eq(workspaceMembers.workspaceId, workspaces.id),
        eq(workspaceMembers.userId, userId)
      )
    )
    .where(and(eq(workspaces.kind, "personal"), eq(workspaces.ownerUserId, userId)))
    .limit(1);
  return rows[0]?.id ?? null;
}

/**
 * Returns the user's personal workspace id, creating it on first use.
 * Idempotent and safe under concurrent first requests: the partial unique index
 * on (owner_user_id) WHERE kind = 'personal' makes losing racers no-ops, and
 * every caller then reads the single winning row.
 */
export async function ensurePersonalWorkspace(userId: string): Promise<string> {
  if (!userId) throw new Error("ensurePersonalWorkspace requires a userId");

  const existing = await findPersonalWorkspaceId(userId);
  if (existing) return existing;

  await db
    .insert(workspaces)
    .values({ kind: "personal", ownerUserId: userId })
    .onConflictDoNothing();

  const rows = await db
    .select({ id: workspaces.id })
    .from(workspaces)
    .where(and(eq(workspaces.kind, "personal"), eq(workspaces.ownerUserId, userId)))
    .limit(1);
  const workspaceId = rows[0]?.id;
  if (!workspaceId) {
    throw new Error("Failed to resolve personal workspace");
  }

  await db
    .insert(workspaceMembers)
    .values({ workspaceId, userId, role: "owner" })
    .onConflictDoNothing();

  return workspaceId;
}

/**
 * Server-side workspace resolution for routes and server actions. The workspace
 * is always derived from the authenticated user, never from client input.
 */
export async function resolveWorkspace(): Promise<WorkspaceContext> {
  const userId = await getUserId();
  const workspaceId = await ensurePersonalWorkspace(userId);
  return { userId, workspaceId };
}
