# Scribe

> An evidence-first RAG knowledge workspace. Upload private documents, ask questions, and inspect the passages behind every answer.

[![Live Demo](https://img.shields.io/badge/Live-scribe--marsh.vercel.app-00C4A0?style=flat-square&logo=vercel)](https://scribe-marsh.vercel.app)
[![Next.js](https://img.shields.io/badge/Next.js-16.1-black?style=flat-square&logo=next.js)](https://nextjs.org)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg?style=flat-square)](LICENSE)

---

## Overview

Scribe is a full-stack document intelligence system built by [Santosh Kumar](https://github.com/marsh15). It ingests PDF, CSV, Markdown, text, and DOCX sources, indexes them with Gemini embeddings, retrieves relevant passages through pgvector, and streams answers with inspectable citations. The three-pane workspace keeps sources, conversation, and evidence visible without hiding the system's confidence or retrieval path.

**Live:** [scribe-marsh.vercel.app](https://scribe-marsh.vercel.app)

## Project walkthroughs

- [How Scribe works](docs/PROJECT_WORKING_EXPLAINED.md) explains the product, ingestion lifecycle, retrieval flow, reliability model, and evidence experience without requiring code context.
- [SDE fresher interview guide](docs/SCRIBE_WBD_SDE_FRESHER_INTERVIEW_GUIDE.md) covers architecture, computer-science fundamentals, trade-offs, likely interview questions, and honest extension ideas.

---

## Features

### Core
- **Multi-Document RAG** — Upload multiple documents and query across all of them. The AI references up to 10 relevant chunks with cosine similarity scoring.
- **Cited Sources** — Chat responses include clickable citations with file name, page number, and chunk index. Click a citation to navigate directly to the source document.
- **Document Viewer** — Preview PDFs inline, view extracted text for TXT/MD/CSV, and browse all indexed chunks with page, section, and character-offset metadata.
- **Smart Error Handling** — Specific error messages for corrupt PDFs, scanned documents, oversized files, and API rate limits. Failed embeddings automatically roll back.
- **Hallucination Prevention** — Strict guardrails ensure the AI only answers based on retrieved context; it will never fabricate sources or fill gaps with general knowledge.

### Infrastructure
- **Async Ingestion Queue** — Uploads are queued and processed in the background with retry support.
- **SaaS Billing** — Razorpay-backed pricing, plan limits, and usage metering.
- **User Session Isolation** — Each Clerk-authenticated user has a fully isolated workspace (files, chats, search results, billing).

### Engineering depth
- **Recoverable Background Jobs** — Atomic job claims, bounded retries, exponential backoff, stale-job recovery, and idempotent completion paths.
- **Private Direct Uploads** — Constrained upload tokens send originals directly to Vercel Blob without proxying large bodies through the app server.
- **RAG Observability** — Retrieval traces capture selected chunks, similarity, latency, and optional evaluation signals.
- **Focused Test Coverage** — Vitest suites cover validation, tenant-safe intake policy, webhook signatures, billing, chunking, cron authorization, and RAG behavior.

### UI/UX
- **Three-Pane Layout** — Resizable sidebar, main content, and RAG inspector with persistent panel sizes.
- **Real-Time RAG Inspector** — Live pipeline visualization (Tokenize → Embed → Vector Search → Build Context → Stream Response) with retrieved chunk preview.
- **Dark & Light Mode** — Smooth animated toggle with proper contrast across all panels.
- **Purposeful Typography** — DM Serif Display for public-facing headlines, DM Sans for product UI, and JetBrains Mono for technical metadata.
- **Chat Avatars** — User/AI avatar indicators with a custom pen nib logomark.
- **Accessible Interaction States** — Visible focus, reduced-motion support, semantic status colors, and mobile touch targets.

---

## Tech Stack

| Layer | Technology |
|-------|------------|
| **Framework** | Next.js 16.1 (App Router), React 19 |
| **Styling** | Tailwind CSS v4, Radix UI, shadcn/ui |
| **Database** | Neon Postgres (Serverless), Drizzle ORM, pgvector |
| **Auth** | Clerk (middleware + per-user data isolation) |
| **AI / ML** | Google Gemini 2.5 Flash, Gemini Embeddings (3072-dim), Vercel AI SDK |
| **Doc Processing** | pdf-parse, mammoth, csv-parse, LangChain text splitters |
| **Layout** | react-resizable-panels |
| **Payments** | Razorpay |
| **Storage** | Vercel Blob |
| **Deployment** | Vercel |

---

## Project Structure

```
app/
├── api/
│   ├── chat/route.ts              # Gemini streaming chat + RAG tool
│   ├── files/[id]/view/           # Serves original uploaded files
│   ├── uploads/                   # Sign + complete upload flow
│   ├── billing/                   # Checkout, portal, usage, verify
│   ├── internal/ingest/run/       # Background ingestion worker
│   └── webhooks/razorpay/         # Payment webhooks
├── chat/                          # Chat UI (new + existing conversations)
├── files/[id]/                    # Document viewer (preview + chunks)
├── upload/                        # File ingestion with drag-and-drop
├── pricing/                       # Pricing page
├── settings/billing/              # User billing management
├── changelog/                     # Product changelog
└── page.tsx                       # Public landing page

components/
├── sidebar.tsx                    # Nav: chats, KB files, user profile, theme toggle
├── three-pane-layout.tsx          # Resizable triple-pane shell
├── rag-inspector.tsx              # Live pipeline + retrieved chunks viewer
├── theme-toggle.tsx               # Animated dark/light toggle
├── theme-provider.tsx             # next-themes wrapper
├── chat-context.tsx               # Shared chat state provider
└── ui/                            # Radix primitives (Button, ScrollArea, Alert, etc.)

lib/
├── auth.ts                        # Auth helpers (Clerk)
├── chunking.ts                    # Text splitting with page/section metadata
├── db-config.ts                   # Drizzle + Neon connection
├── db-schema.ts                   # Tables: files, documents, chats, billing, usage
├── embeddings.ts                  # Google AI embeddings (with retry)
├── search.ts                      # Cosine similarity vector search (user-scoped)
├── flags.ts                       # Feature flags
└── uploads/                       # Upload token signing + plan limits
```

---

## Getting Started

### Prerequisites
- Node.js v20+
- PostgreSQL with `pgvector` extension ([Neon](https://neon.tech) recommended)
- [Clerk](https://clerk.com) account
- [Google AI](https://aistudio.google.com) API key

### Installation

```bash
# Clone
git clone https://github.com/marsh15/SCRIBE.git
cd SCRIBE

# Install dependencies
npm install

# Set up environment variables (see below)
cp .env.example .env.local

# Push database schema
npx drizzle-kit push

# Start dev server
npm run dev
```

---

## Environment Variables

Create a `.env.local` file:

| Variable | Description | Required |
|----------|-------------|----------|
| `NEON_DATABASE_URL` | PostgreSQL connection string (with pgvector) | ✅ |
| `GOOGLE_GENERATIVE_AI_API_KEY` | API key for Gemini Chat & Embeddings | ✅ |
| `NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY` | Clerk publishable key | ✅ |
| `CLERK_SECRET_KEY` | Clerk secret key | ✅ |
| `SUPERMEMORY_API_KEY` | Supermemory API key when optional context memory is enabled | Optional |
| `ENABLE_SUPERMEMORY` | Enables Supermemory wrapping in chat (`true`/`false`) | Optional |
| `BLOB_READ_WRITE_TOKEN` | Vercel Blob token for private Source uploads | Required for uploads |
| `UPLOAD_SIGNING_SECRET` | HMAC secret for upload token signing | Recommended |
| `CRON_SECRET` | Bearer secret used by Vercel Cron for Source intake | Required in production |
| `NEXT_PUBLIC_APP_URL` | Public app URL for checkout redirects | Recommended |
| `FEATURE_BILLING_ENABLED` | Enable billing APIs/UI (`true`/`false`) | Optional |
| `FEATURE_ASYNC_INGESTION_ENABLED` | Enable async ingestion queue | Optional |
| `FEATURE_PUBLIC_LANDING_ENABLED` | Enable public landing page | Optional |
| `RAZORPAY_KEY_ID` | Razorpay key id | Optional |
| `RAZORPAY_KEY_SECRET` | Razorpay key secret | Optional |
| `RAZORPAY_WEBHOOK_SECRET` | Razorpay webhook signing secret | Optional |
| `RAZORPAY_PLAN_PRO_INR` | Razorpay plan identifier for Pro billing | Optional |
| `RAZORPAY_PLAN_TEAM_INR` | Razorpay plan identifier for Team billing | Optional |
| `RAZORPAY_MANAGE_BILLING_URL` | Hosted billing-management URL shown in Settings | Optional |

---

## Scripts

| Script | Command | Description |
|--------|---------|-------------|
| `dev` | `npm run dev` | Start Next.js dev server |
| `build` | `npm run build` | Create optimized production build |
| `start` | `npm start` | Start production server |
| `lint` | `npm run lint` | Run ESLint |
| `test` | `npm run test` | Run unit tests |

---

## Deployment

Deployed on **Vercel**. Push to `main` to trigger auto-deploy.

The background Source intake worker is configured as a daily Vercel Cron Job
(`0 0 * * *`) so deployments remain compatible with Vercel Hobby limits. Upgrade
the project to Pro before changing this schedule to once per minute.

> **Note:** For reliable ingestion on the free tier, documents should be under ~25 pages.

---

## License

MIT
