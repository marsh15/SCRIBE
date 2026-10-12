# Scribe 1.0 baseline behavior

This table was documented from the code on branch `codex/production-readiness` (commit `62ccb1a`), before any Scribe 2.0 change. Measured retrieval numbers for this baseline are in T07 (`benchmarks/baseline-1.0.md`).

| Area | Current behavior | Where |
|---|---|---|
| Retrieval | Dense-only. One query embedding, cosine similarity against `documents.embeddings`, no lexical search, no reranking | [lib/search.ts:40-73](../../lib/search.ts) |
| Similarity threshold | Fixed `0.3`, applied as `gt(similarity, threshold)` in SQL; chat calls it with `limit=10, threshold=0.3` | [lib/search.ts:44](../../lib/search.ts), [app/api/chat/route.ts:210](../../app/api/chat/route.ts) |
| Scoping | Filter by `files.userId` only; no workspace, no RLS | [lib/search.ts:71](../../lib/search.ts) |
| Embeddings | `gemini-embedding-2-preview`, 3072 dimensions | [lib/embeddings.ts:2](../../lib/embeddings.ts) |
| Chunking | Character splitting: 4000 chars, 400 overlap, separators `\n\n`, `\n`, space | [lib/chunking.ts:3-7](../../lib/chunking.ts) |
| Page numbers | Estimated as `ceil(charOffset / totalLength * numPages)`; never exact, no bounding box | [lib/chunking.ts:31-35](../../lib/chunking.ts) |
| PDF extraction | `pdf-parse` flat text; page count only | [lib/ingestion/extract.ts:18-23](../../lib/ingestion/extract.ts) |
| Scanned PDFs | Rejected as unsupported | commit `6f99b06` |
| Duplicate ingestion paths | Two writers: `lib/ingestion/source-intake.ts` and legacy `lib/ingestion/worker.ts` (plus `/api/uploads/complete`) | [lib/ingestion/worker.ts:64](../../lib/ingestion/worker.ts) |
| Re-ingestion | Deletes old chunks before inserting new ones (lossy on failure) | `lib/ingestion/source-intake.ts` |
| Citations | Prompt asks the model to cite; nothing verifies citations | [app/api/chat/route.ts:263-274](../../app/api/chat/route.ts) |
| Fake Sources fallback | `ensureStructuredAnswer` appends "No source links were generated. Please ask to re-run with citations." | [app/api/chat/route.ts:89-108](../../app/api/chat/route.ts) |
| Memory injection | Supermemory wraps the model when `ENABLE_SUPERMEMORY=true`; injected memory is unverifiable and uncitable | [app/api/chat/route.ts:28,177-178](../../app/api/chat/route.ts) |
| Ingestion trigger | Blob callback queues a job; Vercel cron runs once a day (`0 0 * * *`) | [vercel.json](../../vercel.json), ADR-0001 |
| Transactions | `neon-http` driver, no interactive transactions | [lib/db-config.ts](../../lib/db-config.ts) |
| Evaluation | `lib/rag-eval.ts` exists but there is no labeled dataset or recorded baseline | [lib/rag-eval.ts](../../lib/rag-eval.ts) |
