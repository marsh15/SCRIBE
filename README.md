# SCRIBE
> An AI-powered RAG (Retrieval-Augmented Generation) Knowledge Base and Chatbot

## Overview
SCRIBE is an intelligent document management and chatbot application that allows users to seamlessly ingest files (PDF, CSV, MD, TXT, DOCX), process them into chunks, and query them using Google's Gemini AI. It provides a premium, resizable triple-pane interface to navigate chats, visualize the RAG pipeline in real time, and manage the uploaded vector knowledge base.

## Features
- **Multi-Document RAG** — Upload multiple documents and query across all of them simultaneously. The AI references up to 10 relevant chunks across your entire knowledge base.
- **User Session Isolation** — Each Clerk-authenticated user has a fully isolated workspace. Files, chats, and search results are scoped per-user.
- **Document Viewer** — Click any file in the Knowledge Base to view it in a dedicated page with:
  - **Preview tab** — Renders PDFs inline and shows extracted text for TXT/MD/CSV files
  - **Chunks tab** — Browse all indexed chunks with page, section, and character-offset metadata
- **Cited Sources** — Chat responses include clickable citations with file name, page number, and chunk index. Click a citation to navigate to the source document in the same tab for a seamless experience.
- **Dark Mode** — Toggle between light and dark themes via the sidebar. Powered by `next-themes`.
- **Real-Time RAG Inspector** — Side panel shows the live pipeline stages (tokenize → embed → search → build context → stream).
- **Resizable Layout** — Three-pane interface with draggable dividers (sidebar, main content, inspector).
- **Smart Error Handling** — Specific error messages for corrupt PDFs, scanned documents, oversized files, and API rate limits. Failed embeddings automatically roll back file records.

## Tech Stack
| Layer | Technology |
|-------|-----------|
| **Framework** | Next.js 16 (App Router), React 19 |
| **Styling** | Tailwind CSS v4, Radix UI, Motion |
| **Database** | Neon Postgres (Serverless), Drizzle ORM, pgvector |
| **Auth** | Clerk (middleware + per-user data isolation) |
| **AI / ML** | Google Gemini 2.5 Flash, Gemini Embeddings (3072-dim), Vercel AI SDK, Supermemory |
| **Doc Processing** | pdf-parse, mammoth, csv-parse, LangChain text splitters |
| **Layout** | react-resizable-panels |

## Folder Structure
```
app/
├── api/
│   ├── chat/route.ts          # Gemini streaming chat + RAG tool
│   └── files/[id]/view/       # Serves original uploaded files (PDF viewer)
├── chat/                      # Chat UI (new + existing conversations)
├── files/[id]/                # Document viewer (preview + chunks)
└── upload/                    # File ingestion with drag-and-drop

components/
├── sidebar.tsx                # Nav with chats, KB files, theme toggle
├── theme-provider.tsx         # next-themes wrapper
├── theme-toggle.tsx           # Animated dark/light toggle
├── three-pane-layout.tsx      # Resizable triple-pane shell
└── ui/                        # Radix primitives (Button, ScrollArea, etc.)

lib/
├── auth.ts                    # getUserId() helper (Clerk)
├── chunking.ts                # Text splitting with page/section metadata
├── db-config.ts               # Drizzle + Neon connection
├── db-schema.ts               # Tables: files, documents, chats, chatMessages
├── embeddings.ts              # Google Generative AI embeddings (with retry)
└── search.ts                  # Cosine similarity vector search (user-scoped)

proxy.ts                       # Clerk auth middleware (protects all routes)
```

## Prerequisites
- Node.js v20+ (or Bun)
- PostgreSQL with `pgvector` extension (Neon recommended)
- Clerk account
- Google AI API key
- Supermemory API key

## Installation
1. **Clone the repository:**
   ```bash
   git clone https://github.com/marsh15/SCRIBE.git
   cd SCRIBE
   ```
2. **Install dependencies:**
   ```bash
   npm install
   ```
3. **Set up environment variables** (see below).
4. **Push the database schema:**
   ```bash
   npx drizzle-kit push
   ```
5. **Start development server:**
   ```bash
   npm run dev
   ```

## Environment Variables
Create a `.env` or `.env.local` file:

| Variable | Description | Required |
|----------|-------------|----------|
| `NEON_DATABASE_URL` | PostgreSQL connection string (with pgvector) | Yes |
| `GOOGLE_GENERATIVE_AI_API_KEY` | API key for Gemini Chat and Embeddings | Yes |
| `NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY` | Clerk publishable key | Yes |
| `CLERK_SECRET_KEY` | Clerk secret key | Yes |
| `SUPERMEMORY_API_KEY` | Supermemory AI context tool | Yes |

## Scripts
| Script | Command | Description |
|--------|---------|-------------|
| `dev` | `npm run dev` | Starts Next.js dev server |
| `build` | `npm run build` | Creates optimized production build |
| `start` | `npm start` | Starts production server |
| `lint` | `npm run lint` | Runs ESLint |

## Deployment
The app is deployed on **Vercel**. Push to `main` to trigger auto-deploy.

> **Note:** Vercel Hobby has a 10s serverless function timeout. File uploads are capped at 10 MB to stay within this limit.

## License
MIT License
