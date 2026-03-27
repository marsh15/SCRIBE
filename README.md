# ✒️ SCRIBE

> **AI-Powered RAG Knowledge Base & Chatbot** — Upload documents, ask questions, get cited answers.

[![Live Demo](https://img.shields.io/badge/Live-scribe--marsh.vercel.app-00C4A0?style=flat-square&logo=vercel)](https://scribe-marsh.vercel.app)
[![Next.js](https://img.shields.io/badge/Next.js-16.1-black?style=flat-square&logo=next.js)](https://nextjs.org)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg?style=flat-square)](LICENSE)

---

## Overview

SCRIBE is an intelligent document management and chatbot application that lets you ingest files (PDF, CSV, MD, TXT, DOCX), process them into vector embeddings, and query them using Google Gemini AI. It features a premium resizable three-pane interface to navigate chats, visualize the RAG pipeline in real time, and manage your uploaded knowledge base — all scoped per-user.

**Live:** [scribe-marsh.vercel.app](https://scribe-marsh.vercel.app)

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
- **SaaS Billing** — INR-first pricing with dual gateways (Stripe + Razorpay), plan limits, and usage metering.
- **User Session Isolation** — Each Clerk-authenticated user has a fully isolated workspace (files, chats, search results, billing).

### UI/UX
- **Three-Pane Layout** — Resizable sidebar, main content, and RAG inspector with persistent panel sizes.
- **Real-Time RAG Inspector** — Live pipeline visualization (Tokenize → Embed → Vector Search → Build Context → Stream Response) with retrieved chunk preview.
- **Dark & Light Mode** — Smooth animated toggle with proper contrast across all panels.
- **Premium Typography** — DM Serif Display (headlines), DM Sans (body), JetBrains Mono (code/labels).
- **Chat Avatars** — User/AI avatar indicators with a custom pen nib logomark.
- **Micro-Animations** — Stagger-in list items, floating upload icon, pulse-glow active indicators, hover-lift cards.

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
| **Payments** | Stripe, Razorpay |
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
├── auth.ts                        # getUserId() helper (Clerk)
├── chunking.ts                    # Text splitting with page/section metadata
├── db-config.ts                   # Drizzle + Neon connection
├── db-schema.ts                   # Tables: files, documents, chats, chatMessages
├── embeddings.ts                  # Google AI embeddings (with retry)
├── search.ts                      # Cosine similarity vector search (user-scoped)
├── flags.ts                       # Feature flags
└── uploads.ts                     # Upload token signing/verification
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
| `SUPERMEMORY_API_KEY` | Supermemory AI context tool | ✅ |
| `BLOB_READ_WRITE_TOKEN` | Vercel Blob token for file storage | Recommended |
| `UPLOAD_SIGNING_SECRET` | HMAC secret for upload token signing | Recommended |
| `INTERNAL_CRON_SECRET` | Auth secret for `/api/internal/ingest/run` | Recommended |
| `NEXT_PUBLIC_APP_URL` | Public app URL for checkout redirects | Recommended |
| `FEATURE_BILLING_ENABLED` | Enable billing APIs/UI (`true`/`false`) | Optional |
| `FEATURE_ASYNC_INGESTION_ENABLED` | Enable async ingestion queue | Optional |
| `FEATURE_PUBLIC_LANDING_ENABLED` | Enable public landing page | Optional |
| `STRIPE_SECRET_KEY` | Stripe API secret | Optional |
| `STRIPE_WEBHOOK_SECRET` | Stripe webhook signing secret | Optional |
| `RAZORPAY_KEY_ID` | Razorpay key id | Optional |
| `RAZORPAY_KEY_SECRET` | Razorpay key secret | Optional |

---

## Scripts

| Script | Command | Description |
|--------|---------|-------------|
| `dev` | `npm run dev` | Start Next.js dev server |
| `build` | `npm run build` | Create optimized production build |
| `start` | `npm start` | Start production server |
| `lint` | `npm run lint` | Run ESLint |
| `test` | `npx vitest run` | Run unit tests |

---

## Deployment

Deployed on **Vercel**. Push to `main` to trigger auto-deploy.

> **Note:** For reliable ingestion on the free tier, documents should be under ~25 pages.

---

## License

MIT
