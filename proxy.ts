import { clerkMiddleware, createRouteMatcher } from "@clerk/nextjs/server";
import { NextResponse } from "next/server";

const isPublicRoute = createRouteMatcher([
    "/sign-in(.*)",
    "/sign-up(.*)",
    "/api/webhooks/razorpay",
]);

export default clerkMiddleware(async (auth, req) => {
    // Skip auth check for Next.js Server Action POST requests.
    // Server Actions are POST requests to page URLs — Clerk already
    // validates the session via cookie, so intercepting them here
    // causes "Failed to fetch" errors on the client side.
    const isServerAction =
        req.method === "POST" &&
        req.headers.get("next-action") !== null;

    if (isServerAction) return NextResponse.next();

    const pathname = req.nextUrl.pathname;
    const isMarketingRoute =
        pathname === "/" ||
        pathname.startsWith("/pricing") ||
        pathname.startsWith("/changelog");

    if (!isPublicRoute(req) && !isMarketingRoute) {
        await auth.protect();
    }
});

export const config = {
    matcher: [
        // Skip Next.js internals and all static files
        "/((?!_next|[^?]*\\.(?:html?|css|js(?!on)|jpe?g|webp|png|gif|svg|ttf|woff2?|ico|csv|docx?|xlsx?|zip|webmanifest)).*)",
        // Always run for API routes
        "/(api|trpc)(.*)",
    ],
};
