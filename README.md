# SCRIBE
> An AI-powered RAG (Retrieval-Augmented Generation) Knowledge Base and Chatbot

## Overview
SCRIBE is an intelligent document management and chatbot application that allows users to seamlessly ingest files (PDF, CSV, MD, TXT, DOCX), process them into chunks, and query them using Google's Gemini AI. It provides a premium, resizable triple-pane interface to navigate chats, visualize the RAG pipeline in real time, and manage the uploaded vector knowledge base.

## Tech Stack
- **Frontend Framework**: Next.js 16 (App Router), React 19
- **Styling**: Tailwind CSS v4, Radix UI primitives, Framer Motion
- **Database & ORM**: Neon Postgres (Serverless), Drizzle ORM
- **Authentication**: Clerk
- **AI & ML**: Google Generative AI (Gemini 2.5 Flash & Embeddings), Vercel AI SDK, Supermemory 
- **Document Processing**: pdf-parse, mammoth, csv-parse
- **Layout**: react-resizable-panels

## Folder Structure
- `app/` - Next.js App Router containing pages, API routes, and Server Actions.
  - `app/api/` - API endpoints, including the main Gemini + Supermemory chat endpoint.
  - `app/chat/` - Chat UI with resizable layout and history.
  - `app/upload/` - Document ingestion and granular knowledge base management.
- `components/` - React components, including the ThreePaneLayout, RAG Inspector, Sidebar, and UI library.
- `lib/` - Shared utilities, database configurations, search, and chunking logic.
  - `db-schema.ts` - Drizzle ORM tables (`files`, `documents`, `chats`, `chatMessages`).
- `scripts/` - Database migration utilities (e.g., embeddings vector size migrations).
- `middleware.ts` - Clerk authentication and route protection middleware.

## Prerequisites
- Node.js (v20+ recommended)
- PostgreSQL database (Neon recommended for `vector` support)

## Installation
1. Clone the repository:
   ```bash
   git clone https://github.com/marsh15/SCRIBE.git
   cd SCRIBE
   ```
2. Install dependencies:
   ```bash
   npm install
   # or
   bun install
   ```
3. Set up your environment variables (see below).
4. Start the application:
   ```bash
   npm run dev
   # or
   bun run dev
   ```

## Environment Variables
The application requires several API keys to fully function, defined in a `.env` or `.env.local` file.

| Variable | Description | Required |
|----------|-------------|----------|
| `NEON_DATABASE_URL` | PostgreSQL connection string with pgvector extension | Yes |
| `GOOGLE_GENERATIVE_AI_API_KEY` | API Key for Gemini Chat and Embeddings models | Yes |
| `NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY` | Clerk Authentication publishable key | Yes |
| `CLERK_SECRET_KEY` | Clerk Authentication secret key | Yes |
| `SUPERMEMORY_API_KEY` | Supermemory AI tool for agentic user context | Yes |

## Running the Project
- **Development**: `npm run dev` or `bun dev` (starts the Next.js dev server)
- **Production Build**: `npm run build` or `bun run build`
- **Start Production**: `npm start`

## Available Scripts
| Script | Command | Description |
|--------|---------|-------------|
| `dev` | `next dev --webpack` | Starts the application in development mode with Webpack |
| `build` | `next build` | Creates an optimized production build (Turbopack + proxy client flags) |
| `start` | `next start` | Starts the Next.js production server |
| `lint` | `eslint` | Runs ESLint to catch code quality issues |

## License
MIT License
