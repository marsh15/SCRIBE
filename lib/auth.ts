import { auth } from "@clerk/nextjs/server";

export class NotAuthenticatedError extends Error {
    constructor() {
        super("Not authenticated");
        this.name = "NotAuthenticatedError";
    }
}

/**
 * Get the authenticated user's ID.
 * Throws if not signed in — use only in server actions / API routes that require auth.
 */
export async function getUserId(): Promise<string> {
    const { userId } = await auth();
    if (!userId) {
        throw new NotAuthenticatedError();
    }
    return userId;
}

export function isNotAuthenticatedError(error: unknown) {
    return error instanceof NotAuthenticatedError;
}
