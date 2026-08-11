import type { Metadata } from "next";
import "./globals.css";
import { ClerkProvider } from "@clerk/nextjs";
import { ThemeProvider } from "@/components/theme-provider";

export const metadata: Metadata = {
  metadataBase: new URL(process.env.NEXT_PUBLIC_APP_URL ?? "https://scribe-marsh.vercel.app"),
  title: {
    default: "Scribe | Evidence-first document intelligence",
    template: "%s | Scribe",
  },
  description:
    "Upload private documents, ask questions, and inspect cited evidence behind every answer. Built with Next.js, PostgreSQL, pgvector, and Gemini.",
  keywords: ["RAG", "document search", "knowledge base", "citations", "pgvector", "Next.js"],
  authors: [{ name: "Santosh Kumar", url: "https://github.com/marsh15" }],
  creator: "Santosh Kumar",
  openGraph: {
    title: "Scribe | Evidence-first document intelligence",
    description: "Ask private documents questions and inspect the evidence behind every answer.",
    type: "website",
    siteName: "Scribe",
  },
  twitter: {
    card: "summary_large_image",
    title: "Scribe | Evidence-first document intelligence",
    description: "Ask private documents questions and inspect the evidence behind every answer.",
  },
  robots: { index: true, follow: true },
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <ClerkProvider>
      <html lang="en" suppressHydrationWarning>
        <body className="font-sans antialiased">
          <ThemeProvider
            attribute="class"
            defaultTheme="light"
            enableSystem
            disableTransitionOnChange={false}
          >
            {children}
          </ThemeProvider>
        </body>
      </html>
    </ClerkProvider>
  );
}
