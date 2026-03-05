import { SignUp } from "@clerk/nextjs";

export default function SignUpPage() {
    return (
        <main className="min-h-screen flex items-center justify-center bg-background">
            <SignUp
                fallbackRedirectUrl="/chat"
                signInUrl="/sign-in"
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
