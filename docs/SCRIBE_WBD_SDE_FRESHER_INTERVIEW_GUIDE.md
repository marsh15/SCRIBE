# Scribe: Complete Project Theory and WBD SDE Fresher Interview Guide

This is the single preparation document for explaining **Scribe** in an SDE fresher interview. It covers the project, computer-science theory behind it, design decisions, trade-offs, likely questions, and ready-to-speak answers.

> Important: Understand the answers and say them naturally. Do not claim traffic, team size, or production impact that you did not actually have. Replace any sample personal contribution with what you personally implemented.

---

## 1. Project in One Line

**Scribe is a multi-tenant RAG knowledge workspace where users privately upload documents and ask questions whose answers are grounded in retrieved document chunks and linked to citations.**

## 2. Thirty-Second Interview Introduction

> I built Scribe, a full-stack AI knowledge-base application using Next.js, TypeScript, PostgreSQL with pgvector, Clerk, Vercel Blob, and Google Gemini. A user can upload a PDF, DOCX, CSV, Markdown, or text file. The system stores it privately, asynchronously extracts and chunks the text, generates embeddings, and saves them in PostgreSQL. During chat, it embeds the question, performs user-scoped cosine-similarity search, and streams a Gemini answer using only the retrieved evidence. I also added citations, ingestion retries, usage metering, and RAG observability so users can inspect retrieval quality and latency.

## 3. Two-Minute Project Explanation

> The problem I wanted to solve was that normal chatbots can give fluent but unsupported answers, while users often need answers from their own private documents. Scribe uses Retrieval-Augmented Generation, or RAG, to solve that.
>
> The system has two main pipelines. The first is ingestion. The browser requests a constrained private upload token, uploads the original file directly to Vercel Blob, and the server creates an ingestion job. A worker claims queued jobs, extracts text according to file type, splits it into overlapping chunks, creates 3072-dimensional Gemini embeddings in batches, and stores each chunk and vector in PostgreSQL using pgvector. The source moves through visible states such as uploading, queued, processing, retrying, ready, or failed.
>
> The second pipeline is question answering. Every user question must call the knowledge-base search tool. Scribe embeds the query and calculates cosine similarity against that user's document chunks. It selects up to ten chunks above a threshold, formats them with file and location metadata, and passes them to Gemini as evidence. The response streams to the UI and includes links back to the source. Chat messages, retrieved chunks, timings, and optional evaluation scores are persisted.
>
> The most important engineering concerns were tenant isolation, reliability, and explainability. Every data query is scoped by the authenticated Clerk user ID. Queue jobs use atomic state transitions, retries with exponential backoff, stale-job recovery, and `FOR UPDATE SKIP LOCKED` to prevent two workers from claiming the same job. The evidence inspector shows what was retrieved and how long each stage took. If I scaled it further, I would improve hybrid retrieval, add OCR, add stricter authorization tests, and move ingestion to a dedicated queue platform.

---

## 4. Problem Statement

Users have private documents such as policies, research, notes, contracts, and operational guides. Reading every document for each question is slow. A general-purpose LLM may not know these documents and can hallucinate.

Scribe aims to provide:

- private per-user document storage;
- semantic search across all uploaded sources;
- answers constrained to retrieved evidence;
- citations that let the user inspect the original source;
- a reliable ingestion lifecycle with visible status;
- observability for retrieval quality, latency, and grounding;
- SaaS plan limits and usage metering.

## 5. Functional and Non-Functional Requirements

### Functional requirements

- Sign up and sign in.
- Upload PDF, TXT, Markdown, CSV, and DOCX files.
- Extract and index document text.
- Display ingestion state and errors.
- Create, load, and delete chats.
- Ask questions across the user's indexed documents.
- Stream grounded answers with citations.
- Preview sources and indexed chunks.
- Inspect retrieval traces and optional evaluation scores.
- Track billing plan and usage.

### Non-functional requirements

- **Security:** one user must never access another user's files, chats, or search results.
- **Reliability:** failed or interrupted ingestion should be retryable.
- **Idempotency:** repeated callbacks or usage writes must not create incorrect duplicate state.
- **Performance:** uploads should not proxy large files through the application server; chat should stream.
- **Scalability:** background jobs must be claimable by multiple workers without duplicate processing.
- **Observability:** retrieval and generation stages should be measurable.
- **Accessibility:** core workflows target WCAG 2.2 AA, keyboard access, reduced motion, zoom, and adequate touch targets.

---

## 6. Technology Stack and Why It Is Used

| Layer | Technology | Why it fits Scribe |
|---|---|---|
| Frontend and backend | Next.js 16 App Router | One TypeScript codebase, React Server Components, route handlers, server actions, streaming, and Vercel deployment |
| UI | React 19, Tailwind CSS 4, Radix/shadcn | Component composition, responsive design, accessible primitives |
| Language | TypeScript | Static checking across UI, API payloads, database models, and AI tool results |
| Authentication | Clerk | Session management and authenticated user ID for tenant boundaries |
| Database | Neon serverless PostgreSQL | Relational consistency, serverless connectivity, and pgvector support |
| ORM | Drizzle ORM | Type-safe schema and SQL-like query construction |
| Vector search | pgvector | Keeps structured application data and vectors in one database |
| Object storage | Vercel Blob | Private storage for binary originals and direct browser uploads |
| Chat model | Gemini 2.5 Flash | Fast generation and tool calling |
| Embeddings | Gemini embedding model | Produces 3072-dimensional semantic vectors |
| AI orchestration | Vercel AI SDK | Streaming responses, message conversion, and tool calls |
| Parsing | pdf-parse, Mammoth, csv-parse | Extract text from supported source formats |
| Chunking | LangChain text splitter | Recursive, separator-aware chunks with overlap |
| Payments | Razorpay | Subscription checkout and webhook-driven billing state |
| Tests | Vitest | Fast unit tests for policies, validation, billing, signatures, and RAG behavior |

### Why PostgreSQL plus pgvector instead of a separate vector database?

> For this project, PostgreSQL already stores users' source metadata, chats, and ingestion state. pgvector lets me store embeddings next to document chunks and join them with the files table during search. This simplifies consistency, deletion, tenant filtering, backups, and operations. A specialized vector database may offer better distributed scale or retrieval features, but it would add another system and a synchronization problem. PostgreSQL is the simpler choice for the current scale.

---

## 7. High-Level Architecture

```text
                         +----------------------+
                         |      Clerk Auth      |
                         +----------+-----------+
                                    |
                                    v
+-------------+    HTTPS    +-------+-------------------------+
| React UI    | <---------->| Next.js application            |
| Sources     |             | route handlers + server actions|
| Chat        |             +---+-------------+-------------+-+
| Evidence    |                 |             |             |
+------+------+                 |             |             |
       | direct private upload  |             |             |
       v                        v             v             v
+------+------+          +------+------+ +----+-----+ +-----+---------+
| Vercel Blob |          | Neon Postgres| | Gemini   | | Razorpay      |
| originals   |          | + pgvector   | | chat +   | | subscriptions |
+------+------+          +------+------+ | embedding| +---------------+
       |                        ^         +----------+
       | download               |
       v                        |
+------+------------------------+--+
| Source intake worker             |
| extract -> chunk -> embed -> save|
+----------------------------------+
```

### The three main product areas

1. **Sources:** upload, state, preview, deletion, and metadata.
2. **Conversation:** chat history and streaming answers.
3. **Evidence:** citations, retrieved chunks, similarity, latency, and evaluations.

---

## 8. End-to-End Upload and Ingestion Flow

```text
Select file
   -> validate name, MIME type, size, plan
   -> create Source row with status=uploading
   -> issue short-lived constrained Blob token
   -> browser uploads directly to private Blob storage
   -> Blob completion callback validates path/type/size
   -> atomically set Source=queued and create ingestion job
   -> worker claims job
   -> Source=processing
   -> download original
   -> extract text
   -> split into overlapping chunks
   -> batch-generate embeddings
   -> replace old chunks and save new chunks atomically
   -> Source=ready, job=completed
```

### Why direct-to-blob upload?

If the file passed through the Next.js server, the server would use bandwidth and memory and could hit request-body or execution-time limits. A constrained, short-lived token lets the browser upload directly while the server still controls the path, MIME type, maximum size, and callback.

### Source state machine

```text
uploading -> queued -> processing -> ready
                         |
                         +-> retrying -> queued
                         |
                         +-> failed
```

- `uploading`: reservation exists but the upload callback has not completed.
- `queued`: original is stored and a job is waiting.
- `processing`: extraction, chunking, embedding, or persistence is running.
- `retrying`: a retryable error occurred and another attempt is scheduled.
- `ready`: searchable chunks exist.
- `failed`: a terminal error or retry exhaustion occurred.

### Text extraction

| Format | Extraction approach |
|---|---|
| PDF | `pdf-parse`; also returns a page count |
| DOCX | Mammoth raw-text extraction |
| CSV | Parse rows, join columns with spaces, join rows with newlines |
| TXT/Markdown | UTF-8 decoding |

An empty extraction is rejected. Scanned or image-only PDFs currently need OCR and are explicitly reported as unsupported rather than silently indexing no content.

### Chunking

Scribe uses recursive character splitting with:

- chunk size: **4000 characters**;
- overlap: **400 characters**;
- separator preference: paragraph, newline, space, then character boundary.

Metadata includes chunk index, total chunks, estimated page, section, percentage position, character offset, and character length.

### Why overlap chunks?

An important sentence may begin at the end of one chunk and finish in the next. Overlap preserves local context at boundaries, improving retrieval and answer quality. The cost is duplicated text, more embedding storage, and possible retrieval redundancy.

### Why is PDF page location approximate?

The extractor returns flattened text and total pages, not exact character-to-page mappings. Scribe estimates a page from the chunk's relative character position. This is useful but not exact. A stronger design would extract page-by-page and preserve precise page metadata.

---

## 9. Queue, Concurrency, Retry, and Idempotency Theory

### Why asynchronous processing?

Extraction and embedding can be slow and can fail due to provider limits. Keeping all work inside the upload request would cause timeouts and a poor user experience. The queue separates accepting the file from making it searchable.

### How a worker safely claims jobs

The worker selects eligible jobs inside SQL using `FOR UPDATE SKIP LOCKED`, then updates them to `processing`. If two workers run together, one locks a candidate and the other skips it, reducing duplicate processing.

### Retry strategy

Retryable errors are re-queued with exponential backoff. Delay grows roughly as `2^attempts` and is capped. Non-retryable errors, such as unsupported formats or no extractable text, fail immediately. Attempts are capped at five.

### Stale-job recovery

A serverless function can die after claiming a job. Scribe detects jobs left in `processing` beyond a time threshold, returns them to the queue, and marks the source as retrying. It also marks uploads that never complete within 24 hours as failed.

### Idempotency examples

- One ingestion job per file is enforced with a unique database index.
- A repeated Blob callback for the same stored URL is treated as a replay, not a new transition.
- Chunk `(file_id, chunk_index)` is unique.
- Usage events can carry a unique idempotency key.
- Razorpay event `(gateway, event_id)` is unique.
- Re-indexing deletes old chunks and inserts the replacement set in one database batch.

### Interview answer: What is idempotency?

> An operation is idempotent when repeating the same request has the same final effect as performing it once. It matters in distributed systems because callbacks, network requests, and jobs may be delivered more than once. In Scribe, duplicate upload callbacks do not create duplicate jobs, and usage events can use unique idempotency keys so retries do not double-charge the user.

### At-least-once vs exactly-once processing

The practical model is **at-least-once execution with idempotent effects**, not magical exactly-once execution. A job can be retried after an uncertain failure, but uniqueness constraints and atomic replacement make repeated processing safe.

---

## 10. RAG Theory

### What is RAG?

Retrieval-Augmented Generation has three stages:

1. **Retrieve** relevant private knowledge.
2. **Augment** the model prompt with that evidence.
3. **Generate** an answer based on the evidence.

RAG does not train the model on uploaded documents. It retrieves relevant passages at request time.

### What is an embedding?

An embedding is a dense numeric vector representing semantic meaning. The current schema stores **3072 floating-point dimensions** for each chunk. Semantically related passages should be close in vector space even if their exact wording differs.

### Cosine similarity

For vectors `A` and `B`:

```text
cosine_similarity(A, B) = (A dot B) / (||A|| * ||B||)
```

It measures orientation rather than raw magnitude. Scribe uses:

```text
similarity = 1 - cosine_distance
```

The query selects up to **10 chunks** with similarity greater than **0.3**, ordered from highest to lowest.

### Why semantic search instead of SQL `LIKE`?

Keyword search looks for matching text. Semantic search can match meaning. For example, “cancellation policy” may retrieve a chunk containing “terminate the subscription” even if the exact words differ. Keyword search remains valuable for names, codes, and exact phrases, which is why hybrid retrieval is a good future improvement.

### Top-K and threshold trade-off

- Low `K` or high threshold: less noise and lower prompt cost, but may miss evidence.
- High `K` or low threshold: better recall, but more irrelevant context, cost, and possible model confusion.
- Scribe currently uses fixed defaults. A production system should tune them on an evaluation dataset.

### Chunk-size trade-off

- Small chunks improve retrieval precision but may lose surrounding context.
- Large chunks preserve context but may contain several topics and waste tokens.
- Overlap improves continuity but increases duplicate storage and context.

### Retrieval precision and recall

- **Precision:** among retrieved chunks, how many are relevant?
- **Recall:** among all relevant chunks, how many were retrieved?

A strict threshold may improve precision while hurting recall. RAG tuning seeks an acceptable balance for the product's question types.

### Hallucination prevention

Scribe uses several layers:

- every user message is instructed to call the search tool;
- search results contain exact source metadata and content;
- the system prompt says to use only retrieved evidence;
- insufficient evidence must be stated explicitly;
- generated citations must use supplied links;
- traces and optional judge evaluations expose whether answers were grounded.

This reduces hallucination but cannot mathematically eliminate it. LLM instructions are probabilistic. Stronger enforcement could validate citations after generation, use structured output, and block unsupported claims.

### RAG vs fine-tuning

| RAG | Fine-tuning |
|---|---|
| Adds external facts at request time | Changes model behavior/weights through training |
| Easy to update by re-indexing documents | Requires another training cycle |
| Can cite retrieved evidence | Does not naturally provide source citations |
| Good for private, changing knowledge | Good for style, task behavior, or specialized patterns |

For Scribe's changing private documents, RAG is the appropriate primary technique.

---

## 11. End-to-End Chat Flow

```text
User sends message
   -> authenticate user
   -> check current usage allowance
   -> validate messages
   -> save user message before streaming
   -> model calls searchKnowledgeBase tool
   -> embed search query
   -> pgvector cosine search joined with files
   -> filter by files.user_id
   -> record trace and retrieved chunks
   -> model receives formatted evidence
   -> stream answer to browser
   -> save assistant answer and tool parts
   -> complete timing trace
   -> optionally evaluate answer quality
   -> record input/output token usage
```

### Why save the user message before streaming?

The provider or network may fail halfway through the stream. If both messages were saved only after completion, the user's question would be lost. Saving it first preserves intent and allows the UI or system to retry.

### Why streaming?

LLM generation has noticeable latency. Streaming improves perceived performance by showing tokens as they arrive, reduces time to first visible result, and makes long answers feel responsive. It does not reduce total model computation time.

### Tool calling

The model receives a `searchKnowledgeBase` tool with a validated string query. The system prompt requires a tool call for every user message. The tool returns a structured object containing:

- query and status;
- formatted evidence context;
- top-K and threshold;
- embedding, retrieval, and total timings;
- ranked chunks with file, similarity, metadata, preview, and full content;
- trace ID when persistence succeeds.

The model then uses this result to generate the final response.

---

## 12. Database Design

### Core tables

| Table | Responsibility | Important constraints |
|---|---|---|
| `files` | Source metadata, owner, original storage, extracted text, state | indexed by status/update time; `user_id` required |
| `documents` | chunks, metadata, 3072-d embedding | FK to file with cascade; unique file/chunk index |
| `ingestion_jobs` | queue attempts and retry scheduling | one job per file; queue index |
| `chats` | user-owned conversations | string primary key and `user_id` |
| `chat_messages` | persisted user/assistant messages and rich parts | FK to chat with cascade |
| `rag_traces` | query, status, latency, retrieval config, message links | indexed by user/chat/time |
| `rag_trace_chunks` | snapshot of chunks retrieved for a trace | rank index and trace cascade |
| `rag_evaluations` | judge scores and rationale | one evaluation per trace |
| `billing_customers` | gateway customer mapping | unique user |
| `subscriptions` | plan and provider subscription state | status and billing period |
| `usage_events` | append-only metering records | optional unique idempotency key |
| `billing_cycles` | included amount, consumed amount, overage | time-bounded per user |
| `payment_events` | webhook inbox and processing state | unique gateway/event ID |

### Key relationships

```text
User (Clerk ID)
  +-- files 1 ---- * documents
  |       +-- 1 ingestion_job
  +-- chats 1 ---- * chat_messages
  |       +-- * rag_traces 1 ---- * rag_trace_chunks
  |                           +-- 0..1 rag_evaluation
  +-- subscriptions
  +-- usage_events
  +-- billing_cycles
```

### Why cascade delete?

Document chunks have no meaning without their source, and messages/traces have no meaning without their chat. `ON DELETE CASCADE` preserves referential integrity and prevents orphan rows.

### Why store retrieval snapshots?

If a source is later re-indexed, live search results may change. Saving the retrieved chunk preview, score, rank, and metadata gives an audit trail of what supported a particular answer at that moment.

### Relational normalization discussion

The design separates entities that change independently: files, chunks, chats, messages, jobs, traces, and billing. Some trace data deliberately duplicates file names and previews. That is denormalization for historical observability: the trace should remain interpretable even if current source metadata changes.

---

## 13. API and Server Boundaries

### Important route groups

- `/api/sources/reserve`: authenticate, validate allowance, create source, issue upload token.
- `/api/sources/upload-complete`: handle trusted Blob completion and queue the source.
- `/api/sources/process-now`: user-scoped attempt to process a queued source.
- `/api/internal/ingest/run`: cron-protected background worker.
- `/api/chat`: usage gate, RAG tool, Gemini stream, persistence, traces, evaluation.
- `/api/rag/latest`: latest user-scoped trace for the inspector.
- `/api/files/[id]/view`: serve or preview only an owned source.
- `/api/billing/*`: checkout, portal, verification, and usage.
- `/api/webhooks/razorpay`: verify and process provider events.

### Route handler vs server action

- Route handlers are suitable for HTTP APIs, streaming chat, webhooks, callbacks, and cron.
- Server actions are suitable for authenticated UI mutations such as creating/deleting chats or sources with path revalidation.

### Validation

Validation occurs at trust boundaries:

- upload name, extension, MIME type, byte size, plan limit;
- Blob path, size, and content type against the reservation;
- chat message list;
- AI tool input via Zod;
- batch indexes, count, order, length, and payload size;
- webhook signatures;
- environment variables.

---

## 14. Authentication, Authorization, and Security

### Authentication vs authorization

- **Authentication:** Who is the user? Clerk verifies the session and returns a user ID.
- **Authorization:** May this user access this row or operation? Queries filter by that user ID.

Authentication alone is insufficient. A signed-in user must not be able to pass another source or chat ID and access it.

### Multi-tenant isolation

Tenant scoping appears in the most important paths:

- vector search joins chunks to files and filters `files.user_id = currentUser`;
- file listing and deletion filter by owner;
- chat history joins chats and filters the owner;
- trace lookup filters the trace owner;
- manual ingestion accepts an authenticated user scope;
- storage paths include encoded user ID and source ID.

### Protected non-user endpoints

- The ingestion route verifies a bearer cron secret using a timing-safe comparison.
- Payment webhooks require signature verification.
- The Blob callback is public at the middleware layer because the storage provider calls it, but callback data is validated against the reserved source and expected blob metadata.

### HMAC theory

HMAC combines a secret key with message content to create a tamper-evident signature. The upload-signature helper uses HMAC-SHA256 and checks signatures with `timingSafeEqual`. An attacker who changes a signed payload cannot calculate a valid new signature without the secret.

### Security improvements to discuss honestly

- Add PostgreSQL row-level security as defense in depth.
- Add rate limiting per user and IP.
- Virus-scan uploads and enforce content sniffing, not only declared MIME type.
- Encrypt especially sensitive content with managed keys.
- Use short retention periods for raw model/trace content when required.
- Run a formal authorization test matrix for every ID-based route.
- Apply a content-security policy and audit dependency vulnerabilities.

---

## 15. Reliability and Consistency

### Atomic operations

Scribe groups state that must change together:

- upload completion changes source state and inserts the queue job in one SQL statement;
- successful ingestion replaces chunks and marks the source/job complete in one database batch;
- job claiming changes a selected candidate to processing within one database operation.

Atomicity avoids partial states such as `ready` without chunks or a queued source without a job.

### Database constraints as the last line of defense

Application checks improve error messages, but database constraints protect correctness during races:

- unique job per file;
- unique chunk position per file;
- unique webhook event;
- unique usage idempotency key;
- foreign keys and cascades.

### Failure scenarios and behavior

| Failure | System behavior |
|---|---|
| Gemini API key missing | Return service-unavailable message before chat |
| Provider rate limit | Embedding requests retry with backoff |
| Worker crashes | Stale processing job is recovered and queued |
| Duplicate Blob callback | Recognized as replay |
| Empty/scanned PDF | Terminal failure with explicit explanation |
| Old embeddings during re-index | Delete and replacement insert are batched |
| Stream fails | User question was already persisted |
| Billing/trace write fails | Logged; non-core observability writes are generally prevented from destroying the user answer |

### CAP theorem relevance

CAP concerns distributed data systems during a network partition. Scribe relies mainly on managed PostgreSQL and external providers rather than implementing a distributed database. The useful interview point is that cross-system operations—Blob, database, Gemini, Razorpay—cannot share one ACID transaction. Scribe therefore uses state machines, callbacks, retries, idempotency, and reconciliation.

---

## 16. Billing and Usage Metering

Scribe defines Free, Pro, and Team plans with file-size, storage, model-token, embedding-token, and overage limits.

Usage is represented as append-only events:

- model input tokens;
- model output tokens;
- embedding input tokens;
- storage GB-day.

### Why append-only usage events?

Events provide an audit trail, allow aggregation by billing cycle, and are safer than repeatedly overwriting one counter. Idempotency keys prevent known retries from adding the same usage twice.

### Webhook pattern

The application should treat the payment provider webhook as the source of truth for asynchronous subscription changes. Events are signature-verified, stored with a unique provider ID, and processed idempotently.

---

## 17. RAG Observability and Evaluation

Each retrieval trace can store:

- user and chat;
- user and assistant message linkage;
- query;
- top-K and threshold;
- embedding, retrieval, generation, and total latency;
- retrieved chunks, ranks, scores, and previews;
- optional groundedness, relevance, citation support, overall score, verdict, and rationale.

### Why observability matters

A generated answer may look good even when retrieval is poor. Without traces, it is difficult to distinguish:

- retrieval failure;
- irrelevant context;
- model failure despite good context;
- latency in embedding, database search, or generation.

### LLM-as-judge limitations

An optional Gemini judge scores the answer. This is useful for trends and regression detection but is not absolute truth. It can be biased, inconsistent, and correlated with the answering model. Better evaluation combines human-labeled test questions, deterministic citation checks, retrieval metrics, and model-based scoring.

---

## 18. Frontend and User Experience

The interface is organized as a resizable three-pane workspace:

- left: sources/navigation;
- center: conversation;
- right: evidence/RAG inspector.

On narrower screens, secondary panels collapse so one primary task remains visible. The design emphasizes quiet surfaces, visible focus, semantic status, evidence anatomy, and reduced motion.

### Important React/Next.js concepts used

- Server Components for data-oriented rendering where client state is unnecessary.
- Client Components for upload progress, resizable panels, and chat interaction.
- Server actions for authenticated mutations and revalidation.
- Route handlers for streaming, webhooks, and external callbacks.
- Context for shared chat/evidence state.
- Custom upload hook for the reservation, upload, processing, and error state machine.

### Why a custom hook for upload?

The hook encapsulates UI state transitions and network orchestration. Components can render progress and errors without owning the protocol details. This improves separation of concerns and makes the flow easier to test and change.

---

## 19. Complexity and Performance

Let:

- `N` = number of extracted characters;
- `C` = number of chunks;
- `D` = embedding dimensions, currently 3072;
- `M` = total searchable chunks for a tenant or index;
- `K` = returned chunks, currently at most 10.

### Approximate costs

- Text scan/chunk preparation: about `O(N)`.
- Embedding generation: external model cost proportional to input size; local result handling about `O(C * D)`.
- Exact vector comparison: about `O(M * D)` before database optimizations.
- Sorting all scores would be `O(M log M)` conceptually; top-K database execution can optimize this.
- Storage for embeddings: `O(C * D)`.

The migration history initially created an HNSW cosine index, but a later embedding-dimension migration dropped it and the current schema does not recreate one. Therefore, do **not** claim that the present 3072-dimensional search is definitely HNSW-backed. At growing scale, an important improvement is to create and verify a compatible HNSW index. HNSW trades extra memory and index-build cost for much faster approximate retrieval. Whenever the embedding dimension or model changes, the vector column and index must remain compatible and all existing chunks need re-embedding.

### Current performance choices

- direct browser-to-blob upload;
- multipart upload above 5 MB;
- embedding batches of 100 chunks;
- top-K limit of 10;
- asynchronous ingestion;
- streaming generation;
- queue limit capped per worker invocation;
- database indexes for queue lookup and trace access.

### Scaling plan

1. Add load tests and establish latency/error baselines.
2. Use a dedicated durable queue with controlled concurrency and dead-letter handling.
3. Partition work by tenant and add rate limits/fairness.
4. Tune HNSW parameters and retrieval thresholds on labeled data.
5. Add hybrid keyword/vector retrieval and a reranker.
6. Cache repeated query embeddings or retrieval where privacy and freshness allow.
7. Move expensive evaluation out of the chat completion path.
8. Add read replicas/connection pooling only when measurements justify them.

---

## 20. Testing Strategy

The repository uses Vitest. Existing tests cover areas such as:

- upload signatures;
- source intake policy and upload completion behavior;
- batch validation and finalization;
- chunking and webhook behavior;
- chat tool behavior;
- cron authorization;
- RAG observability and evaluation parsing;
- schemas, billing, previews, and user intake routes.

### Testing pyramid for Scribe

1. **Unit tests:** pure validation, retry classification, price calculation, parsing, formatting.
2. **Integration tests:** database ownership filters, job claiming, upload transitions, vector search.
3. **API tests:** auth, invalid payloads, callbacks, chat failure modes, webhook replay.
4. **End-to-end tests:** sign in, upload a known fixture, wait for ready, ask a known question, open the citation.
5. **RAG evaluation suite:** labeled questions with expected source chunks and acceptable answers.
6. **Load/failure tests:** concurrent workers, provider 429s, timeout recovery, large files, and duplicate callbacks.

### Interview answer: How would you test RAG?

> I would test retrieval and generation separately. For retrieval, I would create a fixed corpus and labeled queries, then measure recall@K, precision@K, and reciprocal rank. For generation, I would check groundedness, answer relevance, citation validity, and refusal when evidence is insufficient. I would combine deterministic checks, human review, and an LLM judge, because an LLM judge alone is not reliable enough.

---

## 21. Important Design Decisions and Trade-offs

### Decision 1: Async ingestion

**Benefit:** resilient uploads and shorter request lifetimes.
**Cost:** more states, retries, worker coordination, and eventual consistency.

### Decision 2: pgvector in PostgreSQL

**Benefit:** simpler architecture, joins, ownership filtering, and transactional deletion.
**Cost:** a specialized vector store may scale or tune ANN search better.

### Decision 3: Private object storage

**Benefit:** original binaries do not bloat relational tables and can be previewed.
**Cost:** database and blob state can diverge, requiring callbacks and cleanup.

### Decision 4: Fixed-size overlapping chunks

**Benefit:** simple and predictable.
**Cost:** boundaries are not truly semantic and retrieval can contain duplicates.

### Decision 5: Mandatory retrieval tool call

**Benefit:** encourages evidence-first answers.
**Cost:** adds latency and cost even for conversational messages and is still prompt-enforced.

### Decision 6: Save traces

**Benefit:** debugging, audits, and evaluation.
**Cost:** extra database writes, sensitive content retention, and storage.

### Decision 7: User ID filters in queries

**Benefit:** direct, understandable multi-tenant isolation.
**Cost:** every new query must remember the rule; database RLS would add defense in depth.

---

## 22. Current Limitations and Best Future Improvements

Say these confidently. Knowing limitations shows engineering maturity.

1. **Scanned PDFs are unsupported.** Add OCR and preserve page-level coordinates.
2. **Page numbers are approximate.** Extract per page and store exact page metadata.
3. **Retrieval is dense-vector only.** Add BM25/full-text search, reciprocal rank fusion, and reranking.
4. **Fixed chunking is not document-aware.** Add heading-, paragraph-, table-, and page-aware chunking.
5. **Prompt rules cannot guarantee grounding.** Add structured citations and post-generation claim validation.
6. **Fixed threshold and K are not tuned.** Build a labeled evaluation set and tune by document/query type.
7. **Serverless cron is a basic queue driver.** Use a durable queue for higher throughput and dead-letter handling.
8. **Evaluation can add latency and cost.** Run it asynchronously or sample requests.
9. **Application filters are the primary tenant guard.** Add PostgreSQL row-level security.
10. **Embedding model changes require migration.** Version embeddings, dual-write during migration, and switch indexes safely.

### Strong answer: What would you improve first?

> I would first build a small labeled retrieval dataset and measure recall@K, because without a baseline I could make the system more complex without improving answer quality. Based on those results, my likely next technical change would be hybrid retrieval with a reranker. It would improve exact-term queries and reduce irrelevant context while preserving semantic matching.

---

## 23. Likely Interview Questions with Ready Answers

### Q1. Tell me about your project.

> Scribe is a full-stack multi-tenant RAG application. Users upload private documents, a background pipeline extracts and chunks the text and stores Gemini embeddings in pgvector, and chat retrieves the most similar user-owned chunks before streaming a cited answer. I focused on the complete system rather than only the model call: authentication, upload security, queue retries, database constraints, chat persistence, billing usage, and retrieval observability.

### Q2. What problem does it solve?

> It reduces the time needed to find answers across private documents while making the evidence inspectable. Unlike a generic chatbot, Scribe retrieves relevant passages from the user's own knowledge base and asks the model to answer only from those passages.

### Q3. What was the most challenging part?

> The hardest part was making ingestion reliable across multiple systems. Upload storage, database state, extraction, embedding APIs, and serverless execution can fail independently. I modeled the source as a state machine, used an ingestion job table, atomic queue transitions, retry classification, exponential backoff, uniqueness constraints, and stale-job recovery. This made failures visible and recoverable.

### Q4. How do you prevent users from seeing each other's documents?

> Clerk provides the authenticated user ID, but authorization is enforced in data access. Search joins document chunks to files and filters by the current file owner. File operations, chats, traces, and manual ingestion are similarly user-scoped. Storage paths also include the user and source IDs. I would add PostgreSQL row-level security as defense in depth.

### Q5. Explain the RAG pipeline.

> During ingestion, I extract text, split it into overlapping chunks, embed each chunk, and store the vector with metadata. At query time, I embed the question, calculate cosine similarity against owned chunks, keep the highest-scoring results above a threshold, add them to the model prompt, and stream the generated answer with citation links.

### Q6. Why not send the whole document to Gemini?

> Large documents may exceed the context window, increase latency and cost, and introduce irrelevant text that distracts the model. Retrieval selects a small relevant context. It also scales the knowledge base beyond one prompt and provides chunk-level citations.

### Q7. What happens if no relevant chunk is found?

> The tool returns an explicit empty result and the model is instructed to say that sufficient information was not found instead of filling the gap from general knowledge. This is important because a correct refusal is safer than an unsupported answer.

### Q8. Why cosine similarity?

> Embedding direction represents semantic meaning, while vector magnitude is usually less important. Cosine similarity normalizes magnitude and compares direction, making it a standard and interpretable choice for text embeddings.

### Q9. Why use a threshold as well as top-K?

> Top-K always returns a fixed number even if every result is poor. A threshold rejects weak matches. Together, K limits context size while the threshold maintains a minimum relevance level.

### Q10. How do you handle API rate limits?

> Embedding calls detect HTTP 429 responses and retry with increasing delay, using a provider retry hint when available. Jobs also retry later when intake failures are transient. For larger scale, I would add global provider-aware concurrency control, jitter, and a dead-letter queue.

### Q11. What if two workers take the same job?

> Candidate jobs are selected with row locking and `SKIP LOCKED`, and claimed in SQL. One worker locks the candidate, so another worker skips it. Unique constraints and idempotent replacement provide an additional safety layer if execution is repeated.

### Q12. Why does eventual consistency appear here?

> A file can be uploaded before it becomes searchable because ingestion is asynchronous. The UI exposes uploading, queued, processing, retrying, ready, and failed states so the user understands that delay. The benefit is a more reliable and scalable request path.

### Q13. How do citations work?

> Retrieved chunks are formatted with a rank, file ID, file name, chunk location, and source link. The model is instructed to use those exact links. Clicking a citation opens the source viewer. The trace also stores the retrieved rank and preview for auditability.

### Q14. How do you detect hallucinations?

> I cannot guarantee detection from a prompt alone. Scribe records the evidence used and can run a judge that scores groundedness, relevance, and citation support. A stronger production approach would also split the answer into claims, verify each claim against cited chunks, validate every citation deterministically, and use human-labeled regression tests.

### Q15. How is chat history persisted safely during streaming?

> The user message is saved before generation starts. If the provider fails mid-stream, the question remains. The assistant answer and tool parts are saved in the completion callback. Duplicate user-message insertion is treated as a non-fatal retry case.

### Q16. What database indexes matter?

> The queue has an index on status, next retry time, and creation time. Traces are indexed for user/chat/time access. Unique indexes enforce one job per file, one chunk index per file, one evaluation per trace, and one payment event per gateway event ID. The migration history once had a vector HNSW index, but it was dropped during an embedding-dimension change and is not recreated by the current schema, so adding a compatible HNSW index is a priority before large-scale vector search.

### Q17. What is HNSW?

> Hierarchical Navigable Small World is an approximate nearest-neighbor graph index. It connects vectors in layers, allowing search to navigate quickly toward nearby vectors instead of comparing every row. It improves query speed at the cost of memory, index construction time, and approximate rather than guaranteed exact results.

### Q18. Why are transactions important here?

> Several state changes must be all-or-nothing. For example, marking a source queued without creating its job would leave it stuck. Replacing chunks partially could make a ready source inconsistent. Atomic SQL operations and database batches reduce these invalid intermediate states.

### Q19. How would you scale to one million documents?

> I would measure first, then separate ingestion into a durable queue with autoscaled workers, enforce per-tenant fairness, batch provider calls, and partition or shard data if PostgreSQL limits were reached. For retrieval, I would tune HNSW, filter efficiently by tenant, consider namespace partitioning, add hybrid retrieval and reranking, and cache safe repeated work. I would also move evaluations off the synchronous chat path and add SLO-based monitoring.

### Q20. How would you change the embedding model?

> I would version embeddings rather than altering them in place. I would add a new vector column or index namespace, backfill chunks in batches, dual-write new content, compare retrieval quality, switch reads after validation, and remove the old version later. Vector dimensions and index operator classes must match the new model.

### Q21. What is the difference between horizontal and vertical scaling here?

> Vertical scaling gives a database or worker more CPU and memory. Horizontal scaling adds more worker instances or database partitions. Scribe's worker claim protocol supports horizontal worker concurrency, while the managed database can initially scale vertically. At higher load, tenant-aware partitioning and distributed queues become relevant.

### Q22. What SOLID or design principles appear in the code?

> Separation of concerns is the clearest example. Extraction, chunking, embeddings, search, intake policy, storage, billing, and observability are separate modules. Pure policy functions accept adapters, which is similar to dependency inversion and makes them unit-testable without real storage or a database. The upload hook encapsulates client orchestration rather than coupling it to presentation components.

### Q23. Why use TypeScript and Zod together?

> TypeScript checks code at compile time, but external input is still unknown at runtime. Zod validates tool and API data at runtime and can narrow it to a safe TypeScript type. They solve different parts of the type-safety problem.

### Q24. What happens when a source is deleted?

> The operation first verifies ownership, deletes the private blob if present, and deletes the file row. Foreign-key cascades remove dependent document chunks and ingestion jobs. This keeps storage and relational data aligned, although production cleanup should also reconcile any external deletion failure.

### Q25. Why Next.js instead of separate React and Express applications?

> For a project of this size, Next.js provides UI, server rendering, server actions, HTTP route handlers, middleware, and streaming in one TypeScript repository. This reduces deployment and shared-type complexity. Separate services would become valuable when independent scaling, ownership, or runtime requirements justify them.

### Q26. How do feature flags help?

> Billing, async ingestion, the public landing page, optional memory, and RAG evaluation can be enabled independently. Flags reduce rollout risk and let the core product function when optional integrations are not configured.

### Q27. What did you learn?

> I learned that an AI feature is a distributed system, not just a model API call. Retrieval quality depends on ingestion and metadata; reliability depends on state transitions and idempotency; privacy depends on authorization in every query; and answer quality is difficult to improve without traces and evaluation data.

### Q28. Tell me about a failure and how you improved it.

> A realistic failure mode was losing state when a long-running request or model stream failed. I changed the design so uploads create durable source and job records, processing can be retried, stale jobs are recovered, and user chat messages are saved before streaming. The general lesson was to persist intent before unreliable external work and make repeated execution safe.

### Q29. Why should we hire you as a fresher?

> I may be early in my career, but Scribe demonstrates that I can connect fundamentals to a complete system. I worked across UI, APIs, authentication, relational modeling, vector search, background jobs, failure handling, testing, and observability. More importantly, I can explain trade-offs, identify current limitations, and learn from measurements rather than treating a working demo as finished engineering.

### Q30. Why WBD?

> WBD operates products where content, metadata, search, reliability, personalization, and user experience meet at large scale. Scribe gave me practical experience with document pipelines, retrieval, streaming interfaces, multi-user data isolation, and observability. I want to apply those foundations to consumer-scale media systems while learning from engineers who handle much larger traffic, availability, and data challenges.

Do not memorize the WBD answer word for word. Add one genuine reason based on the exact team and job description.

---

## 24. Behavioral Stories from Scribe Using STAR

### Story A: Reliability problem

**Situation:** Document processing depended on several slow external and serverless steps.
**Task:** Prevent uploads from becoming silently stuck or partially indexed.
**Action:** Added explicit states, durable jobs, atomic claims, retry classification, exponential backoff, stale-job recovery, and unique constraints.
**Result:** The design can expose failure, retry transient errors, and safely recover interrupted jobs.

### Story B: Security boundary

**Situation:** The product stores private documents for multiple users.
**Task:** Prevent cross-user access even when IDs are guessed.
**Action:** Used Clerk for identity and added owner filters to search, chat, file, trace, and processing queries; validated private storage reservations and callbacks.
**Result:** Retrieval and source operations stay inside the authenticated tenant boundary.

### Story C: Debugging RAG quality

**Situation:** A plausible answer alone did not reveal whether retrieval was correct.
**Task:** Make the RAG pipeline inspectable.
**Action:** Stored query, chunks, ranks, similarities, timing stages, answer linkage, and optional evaluation scores; exposed them in an evidence inspector.
**Result:** Retrieval problems and generation problems can be diagnosed separately.

### Story D: User experience under latency

**Situation:** Uploading, indexing, and generation are not instant.
**Task:** Keep the application understandable and responsive.
**Action:** Used direct uploads with progress, explicit source states, a process-now fallback, streaming chat, and a three-pane Sources/Conversation/Evidence layout.
**Result:** Users see progress and evidence instead of waiting on an unexplained blocking request.

---

## 25. CS Fundamentals Connected to Scribe

### Operating systems and concurrency

- Race conditions: two workers claiming one job.
- Mutual exclusion: database row locks.
- Starvation/fairness: oldest queued jobs are selected first; tenant fairness is a future concern.
- Process failure: stale lease recovery after a worker dies.
- Batching: reduces network overhead but increases per-batch work and latency.

### Database management systems

- Primary and foreign keys.
- Unique and secondary indexes.
- ACID transitions and transactions.
- Normalization for core entities; intentional denormalization for trace history.
- Isolation and locks with `SKIP LOCKED`.
- Vector index for approximate nearest neighbors.

### Computer networks

- HTTPS APIs and bearer authorization.
- Direct uploads reduce an application-server hop.
- Streaming sends incremental response data.
- Retries require backoff because immediate retry can worsen overload.
- Webhooks are asynchronous server-to-server callbacks and can be duplicated.

### Object-oriented/design principles

- Modules have focused responsibilities.
- Policy logic is separated from infrastructure using adapters.
- State machines model valid lifecycle transitions.
- Interfaces/types specify contracts between layers.

### Data structures and algorithms

- Arrays for ordered chunks and messages.
- Sets for duplicate batch-index detection.
- Maps for matching AI tool calls with tool results.
- Vectors and dot products for semantic similarity.
- Ranked top-K retrieval.
- Graph-based HNSW index for approximate neighbor search.

---

## 26. WBD-Oriented System Design Follow-Ups

WBD may care about content platforms, streaming, metadata, personalization, high availability, and scale. Connect Scribe honestly without pretending it is a video-streaming system.

### If asked: How is this relevant to a media company?

> The domain is different, but several engineering patterns transfer directly: ingesting content and metadata, asynchronous processing, searchable indexes, user isolation, low-latency retrieval, streaming responses, observability, and graceful failure. At WBD scale, the infrastructure would be larger and more distributed, but the fundamentals remain useful.

### If asked to adapt Scribe for subtitle/transcript search

> I would ingest transcripts with exact title, season, episode, language, start-time, and end-time metadata. Chunks would follow speaker and scene boundaries rather than only characters. Retrieval would combine semantic vectors with metadata filters and exact keyword search. Citations would deep-link to the playback timestamp. Rights and region filters would be enforced before retrieval.

### If asked about availability

> I would define SLOs separately for upload acceptance, indexing delay, search latency, and chat availability. External AI failure should not make stored sources or chat history unavailable. Queues should buffer ingestion, workers should retry safely, and the UI should degrade with clear state. Metrics, alerts, tracing, and runbooks should follow those SLOs.

---

## 27. Demo Script for an Interview

Keep the demo under five minutes.

1. **Open Scribe:** “The workspace has Sources, Conversation, and Evidence.”
2. **Upload a small known PDF:** point out direct upload progress and source states.
3. **Wait until ready:** explain extraction, chunking, embedding, and the job worker.
4. **Ask a question whose answer exists:** show streaming and citation.
5. **Open the citation:** show the source and chunk metadata.
6. **Show Evidence inspector:** explain similarity ranks and stage latency.
7. **Ask an unsupported question:** demonstrate the intended refusal behavior.
8. **Close with one limitation:** mention OCR or hybrid retrieval and how you would add it.

Have a ready source already indexed in case an external API is slow during the interview.

---

## 28. Resume Bullets

Use only bullets that match your actual contribution.

- Built **Scribe**, a multi-tenant RAG knowledge workspace using Next.js, TypeScript, PostgreSQL/pgvector, Clerk, Vercel Blob, and Google Gemini.
- Designed an asynchronous document-ingestion pipeline for PDF, DOCX, CSV, Markdown, and text with private direct uploads, chunking, batched embeddings, retries, and stale-job recovery.
- Implemented user-scoped cosine-similarity retrieval and streaming grounded answers with source citations and an evidence inspector.
- Added RAG observability covering retrieved chunks, similarity ranks, embedding/retrieval/generation latency, and optional groundedness evaluation.
- Enforced reliability with atomic state transitions, idempotency keys, row locking, uniqueness constraints, and webhook verification.

Avoid adding invented percentages. Measure latency, test counts, or deployment metrics before quantifying them.

---

## 29. Short Rapid-Revision Notes

- Scribe = private, multi-tenant, evidence-first RAG workspace.
- Two pipelines = ingestion and query answering.
- Originals in private Blob; metadata/chunks/vectors in PostgreSQL.
- 4000-character chunks, 400 overlap.
- 3072-dimensional embeddings.
- Cosine similarity = `1 - cosine distance`.
- Current retrieval = top 10, threshold above 0.3.
- User isolation happens at every data query, especially vector search.
- Queue claim uses `FOR UPDATE SKIP LOCKED`.
- Retryable errors use exponential backoff; maximum five attempts.
- Stale processing jobs are recovered.
- Direct uploads reduce server bandwidth and timeout risk.
- User chat message is stored before streaming.
- Traces separate retrieval quality from generation quality.
- pgvector reduces operational complexity; specialized vector DB may scale further.
- RAG is for changing private knowledge; fine-tuning is mainly for behavior/style.
- Biggest honest limitations: no OCR, approximate PDF pages, dense-only retrieval, prompt-based grounding.
- Best next step: evaluation dataset, then hybrid retrieval plus reranking.

---

## 30. Final Interview Checklist

Before the interview, make sure you can explain without looking at notes:

- the 30-second and two-minute pitches;
- upload-to-ready state transitions;
- question-to-citation RAG flow;
- cosine similarity and embeddings in simple language;
- why chunk size and overlap matter;
- why asynchronous jobs, retry, and idempotency matter;
- how tenant isolation is enforced;
- the main database tables and relationships;
- one technical challenge using STAR;
- three current limitations and practical improvements;
- why your experience is relevant to the exact WBD role.

Also prepare normal fresher topics outside this project: arrays, strings, linked lists, stacks/queues, trees, graphs, sorting/searching, time complexity, OOP, SQL joins/indexes/transactions, OS processes/threads/deadlocks, networking basics, and two or three behavioral stories.

---

## 31. Best Final Closing Answer

> Scribe taught me to think beyond a happy-path AI demo. I had to connect data modeling, asynchronous processing, authorization, semantic retrieval, streaming, billing, testing, and observability. The current system is intentionally simple enough to operate, but I can identify where it would need stronger queues, retrieval evaluation, hybrid search, OCR, and database-level tenant controls. That combination of building, measuring, and recognizing trade-offs is what I would bring to an entry-level SDE role.
