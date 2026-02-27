import { currentUser } from "@clerk/nextjs/server";

/**
 * Get the authenticated user's ID.
 * Throws if not signed in — use only in server actions / API routes that require auth.
 */
export async function getUserId(): Promise<string> {
    const user = await currentUser();
    if (!user?.id) {
        throw new Error("Not authenticated");
    }
    return user.id;
}
