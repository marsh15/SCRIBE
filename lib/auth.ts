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

/**
 * True only when the signed-in user's Clerk session token carries `metadata.role === "admin"`
 * (see types/global.ts). Fails closed: signed out, no claim, or a session token template that does not
 * include `metadata` all return false. Set the role in the Clerk dashboard (user public metadata
 * `{"role": "admin"}`) and add `{"metadata": "{{user.public_metadata}}"}` to the session token claims.
 */
export async function isAdmin(): Promise<boolean> {
    const { userId, sessionClaims } = await auth();
    return Boolean(userId) && sessionClaims?.metadata?.role === "admin";
}
