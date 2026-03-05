import { SignIn } from "@clerk/nextjs";

export default function SignInPage() {
    return (
        <main className="min-h-screen flex items-center justify-center bg-background">
            <SignIn
                fallbackRedirectUrl="/chat"
                signUpUrl="/sign-up"
                appearance={{
                    elements: {
                        rootBox: "mx-auto",
                        card: "shadow-none border border-border",
                    },
                }}
            />
        </main>
    );
}
