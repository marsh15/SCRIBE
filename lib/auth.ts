import { auth } from "@clerk/nextjs/server";

/**
 * Get the authenticated user's ID.
 * Throws if not signed in — use only in server actions / API routes that require auth.
 */
export async function getUserId(): Promise<string> {
    const { userId } = await auth();
    if (!userId) {
        throw new Error("Not authenticated");
    }
    return userId;
}
