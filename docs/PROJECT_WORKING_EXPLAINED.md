# How Scribe works

Scribe is an AI knowledge workspace. A user uploads private documents, asks questions about them, and gets answers tied back to evidence.

It is a complete document-to-answer system, not only a chatbot. It stores source documents, extracts their text, divides the text into searchable pieces, turns those pieces into numeric representations, retrieves the most relevant pieces for each question, and asks an AI model to answer only from that retrieved evidence.

This document explains the project conceptually, without code.

> This describes the original pipeline, which is what `INGEST_ENGINE=legacy` and `RETRIEVAL_ENGINE=legacy` (the defaults) still run: whole-file extraction, overlapping character chunks, dense-only search, no claim verification. The Python engine in Scribe 2.0 differs in each of those steps; see [`scribe2/ARCHITECTURE.md`](scribe2/ARCHITECTURE.md).

## The big picture

Scribe has three major responsibilities:

1. Source intake. Scribe accepts documents from a user, stores the originals privately, extracts text, splits it into chunks, and prepares the chunks for search.
2. Evidence-based chat. When the user asks a question, Scribe searches the user's own indexed documents, builds a context package from the best matches, and gives that context to the AI assistant.
3. Workspace management. Scribe keeps each user's files, chats, billing usage, ingestion state, and retrieval traces separate and visible.

The product is evidence-first. An answer is not free-floating AI text. A good Scribe answer shows where it came from, which document supported it, and why the system considered that content relevant.

## Main product concept

Scribe is built around three everyday user actions:

1. Add sources. The user uploads documents such as PDFs, text files, Markdown files, CSV files, or DOCX files. These become the user's private knowledge base.
2. Ask questions. The user asks natural-language questions in chat. The chat searches the uploaded documents first and does not answer from general memory.
3. Inspect evidence. The user can open citations, view retrieved chunks, inspect the retrieval pipeline, and check whether the answer is grounded in real source material.

This is why the interface is organized around Sources, Conversation, and Evidence.

## Core domain ideas

### Source

A Source is an original document uploaded by a user. It includes the original file, its extracted text, its indexed chunks, and the metadata used for citations.

A Source is private to the user who uploaded it. Other users must not be able to search, preview, cite, or delete it.

### Source intake

Source intake is the lifecycle that turns an uploaded file into searchable evidence. It includes:

- reserving a private upload
- storing the original document
- marking the Source as queued
- extracting text
- splitting the text into chunks
- generating embeddings
- saving searchable chunks
- marking the Source as ready

Uploading a document and making it searchable are separate steps. Uploading stores the file. Intake makes it usable by the RAG system.

### Source status

Every Source has a visible state:

- uploading: the upload was reserved but has not finished
- queued: the file is stored and waiting to be processed
- processing: extraction, chunking, or embedding is happening
- retrying: a previous attempt failed, but the system will try again
- ready: the Source can be searched and cited
- failed: processing could not be completed

Document processing can take time, fail, or be retried. Scribe shows that to the user instead of hiding it.

### Chunk

A chunk is a section of extracted document text. Large documents are too big to search or send to an AI model whole, so Scribe breaks them into smaller pieces.

Chunks keep useful metadata, such as approximate page, section, order, and character position. The product uses it to show citations and document locations.

### Embedding

An embedding is a numeric representation of text. Text with similar meaning should produce embeddings that are close together in vector space.

Scribe creates embeddings for document chunks and for each user question, so it can compare a question against stored document meaning and not only exact words.

### Vector search

Vector search is the retrieval step. Scribe compares the embedding of the user's question with the embeddings of document chunks and selects the most similar chunks as evidence.

The result is a ranked set of document passages, which the AI model is expected to use when answering.

### RAG

RAG means Retrieval-Augmented Generation. In Scribe, the flow is:

1. Retrieve relevant chunks from the user's knowledge base.
2. Augment the AI prompt with those chunks.
3. Generate an answer grounded in that evidence.

The model does not need to memorize a user's private documents. The app retrieves the right evidence when the question arrives and gives it to the model as context.

## How uploading works

The upload process is designed around privacy and reliability.

First, the user chooses a file. Scribe checks whether the user may upload it, based on file size, plan limits, and supported file type.

Next, Scribe reserves a private upload location. The browser receives a constrained upload token, so it can upload only the intended file under the intended conditions.

After the file lands in private storage, Scribe records that the upload completed and creates a queued ingestion job. At this point the file exists as a Source, but it may not be searchable yet.

The ingestion worker then processes queued jobs. It downloads the private original, extracts text, chunks it, embeds the chunks, stores the results, and marks the Source ready.

This keeps original documents available for preview and citation, and lets the heavy processing happen separately from the initial upload.

## How ingestion works

Ingestion turns a file into knowledge. It has five stages:

1. Load the original. The system reads the uploaded file from private storage. Some older legacy flows may read from stored base64 data, but the intended design is private object storage.
2. Extract text. Each document type has its own extraction strategy. PDFs are parsed as PDF text, DOCX files are converted to raw text, CSV files are flattened into text rows, and plain text and Markdown are read directly.
3. Split text into chunks. The extracted text is divided into overlapping sections. The overlap helps preserve context across chunk boundaries, so a sentence or idea that spans two chunks is less likely to be lost.
4. Generate embeddings. Each chunk is sent to the embedding model, which returns a vector representation.
5. Save the index. Scribe stores the chunk text, metadata, and embedding in the database. When that is complete, the Source is ready for retrieval.

If ingestion fails, Scribe records the error. Some failures are retryable: temporary provider or infrastructure problems can be retried, while unsupported or empty documents usually become terminal failures.

## Why there is a queue

Document processing is slower and more fragile than a normal page request. A large document may need extraction, chunking, embedding, database writes, and usage metering, and those steps should not depend on one browser request staying open.

The queue gives Scribe a controlled background workflow. The worker can claim jobs, process them, retry them, and recover jobs that got stuck during a server timeout or deployment interruption. That is why the product can show states such as queued, processing, retrying, and failed.

On Vercel, the queue is driven by a protected internal route that a scheduled job normally calls. There is also a "process now" path, so a queued Source can be advanced manually from the UI.

## How chat works

Chat follows one rule: every user message searches the knowledge base before the assistant answers.

When a user sends a message:

1. Scribe identifies the authenticated user.
2. Scribe checks usage and plan limits.
3. The user's message is saved to the chat history.
4. The AI assistant is given a search tool.
5. The assistant must call the search tool before answering.
6. Scribe embeds the user's query.
7. Scribe retrieves the most relevant chunks from that user's documents.
8. The retrieved chunks are formatted as evidence context.
9. The AI model streams an answer based on that context.
10. The assistant message, tool results, usage, and retrieval trace are saved.

The assistant is instructed not to guess. If the retrieved documents do not support an answer, it should say the information was not found in the user's documents.

## How citations work

A citation links an AI answer to a Source.

When search returns chunks, Scribe formats each one with its source information: file name, file link, chunk rank, estimated page or section, and content. The assistant can then refer to those exact sources in the answer.

Citation links point into the document viewer, so the user can move from the answer to the evidence instead of simply trusting the generated response. Citations are part of the answer, which matches the product principle that evidence should be visible and inspectable.

## How the Evidence inspector works

The Evidence inspector is the right-side view that shows what happened during retrieval. It shows the RAG pipeline as visible stages:

- tokenize query
- embed query
- vector search
- build context
- stream AI response

It also shows operational details such as latency, number of RAG calls, retrieved chunks, similarity scores, and evaluation results when available.

RAG systems can fail silently. A normal chatbot may give a confident answer even when retrieval was poor. Scribe exposes retrieval behavior so the user can check whether the answer had meaningful support.

## RAG observability

Scribe records retrieval traces. A trace connects a user question, the retrieved chunks, the generated answer, and timing information. It answers questions such as:

- What query was used for retrieval?
- Which chunks were retrieved?
- Which files did those chunks come from?
- How similar were the chunks to the query?
- How long did embedding, retrieval, and generation take?
- Which assistant answer used this evidence?

The project also has an evaluation step. After an answer is generated, Scribe can judge whether it was grounded, relevant, and supported by citations. This is a quality-control layer for the RAG system.

## User isolation

Scribe is a multi-user product, so user isolation is essential.

The authenticated user's ID is attached to Sources, chats, usage records, traces, and billing data. Search is scoped to the current user, so a question can retrieve only that user's documents.

This tenant boundary is one of the most important security and product rules in the system. Without it, private knowledge bases would leak across accounts.

## Database role

The database is the system of record. It stores:

- uploaded Source metadata
- extracted text
- document chunks
- embeddings
- chat sessions
- chat messages
- RAG traces
- retrieved trace chunks
- RAG evaluations
- ingestion jobs
- billing customers
- subscriptions
- usage events
- payment events

It is also part of the retrieval engine. Because it supports vector data, the same database that tracks files and chats runs similarity search over document embeddings.

## Storage role

Private object storage holds the original uploaded documents. The database holds metadata and extracted or indexed text. Originals can be large and binary, which makes them a poor fit for relational tables.

Keeping the original lets Scribe support document previews, source deletion, reprocessing, and stronger citation behavior.

## Authentication role

Authentication identifies the current user and protects private routes. Most important operations depend on the current user:

- listing Sources
- uploading a Source
- deleting a Source
- viewing a document
- creating a chat
- loading chat history
- searching documents
- recording usage
- managing billing

Authentication is more than a login feature. It is the boundary that keeps the knowledge base private.

## Billing and usage

Scribe includes SaaS billing concepts. Plans define limits such as maximum file size, storage allowance, included model input tokens, included model output tokens, included embedding tokens, and whether overage is allowed.

Usage events are recorded for:

- model input tokens
- model output tokens
- embedding input tokens
- storage usage estimates

The project can calculate whether a user is within included usage or projected to exceed it. On free plans overage is not allowed, so chat can be blocked when the quota is exhausted. Paid plans can allow metered overage.

Billing is feature-flagged, so the core product can run with billing disabled during development or portfolio use.

## Payment system

The project is prepared for Razorpay-backed subscriptions. The payment layer handles customer records, subscriptions, plan identifiers, payment events, and webhook signature verification. Verification matters because payment events should be trusted only when they really came from the provider.

Payment events update the user's commercial state, while usage events measure how much of the product the user consumed.

## Feature flags

Feature flags let the same project run in different modes. The main flags control whether billing is enabled, whether async ingestion is enabled, and whether the public landing page is enabled.

A development environment can keep billing off while still testing uploads and chat. A production environment can enable commercial flows and scheduled ingestion.

## Main user interfaces

### Landing page

The public page introduces Scribe: upload documents, ask questions, and inspect cited answers.

### Upload and Sources page

Users add documents and manage the Source library here. The page shows upload progress, intake state, failures, retry actions, and deletion. It also polls for pending ingestion states, so users can watch queued or processing documents become ready.

### Chat page

Users ask questions here. A new chat can be created from an initial prompt, and existing chats load their saved message history. The chat UI streams assistant responses and shows when evidence retrieval is active.

### Document viewer

The document viewer lets users inspect an uploaded Source. Depending on the file type and whether the original is available, it can preview the original document or show extracted text and chunks. Citations should be inspectable, and this page lets the user move from an answer back to the supporting Source.

### Evidence inspector

The Evidence inspector explains the retrieval process behind an answer. It is the diagnostic surface for RAG.

### Billing and pricing pages

These pages show plans, usage, checkout, and billing management when billing is enabled.

### Admin analytics

The admin analytics surface is for inspecting product or system activity at a higher level.

## Why the three-pane layout matters

The product is not a single-column chatbot. It uses a three-pane model:

- left area: navigation and workspace context
- center area: the active task, such as upload, chat, or document viewing
- right area: evidence and retrieval diagnostics

The user can ask a question and inspect the evidence without losing context.

## Error handling philosophy

Scribe tries to make failures understandable. Upload and ingestion errors are translated into user-facing states. A scanned PDF with no extractable text should not fail mysteriously, a missing API key should produce a configuration message, and a queued job that times out should be recoverable.

The general rule is that uncertainty should be visible. If the system cannot process a file or cannot find evidence, it says so plainly.

## Reliability concepts

The project uses several reliability patterns:

- queued background work for ingestion
- retry scheduling for transient failures
- stale job recovery after timeouts
- idempotent usage records for some events
- private upload completion callbacks
- database retry wrappers around fragile operations
- explicit Source statuses
- protected internal ingestion endpoint

AI and document workflows often depend on external services, so the app has to survive provider errors, network failures, function timeouts, duplicate callbacks, and partial completion.

## Security and privacy concepts

Scribe's security model rests on a few practical rules:

- users must be authenticated for private operations
- every Source belongs to one user
- search is scoped to the current user
- uploaded originals are stored privately
- upload tokens are constrained
- cron ingestion requires a bearer secret
- payment webhooks are signature-checked
- deletion is owner-scoped

The main privacy promise is that a user's documents are not globally searchable. They belong to that user's private workspace.

## AI model responsibilities

The AI model generates language. It does not decide which documents the user owns or where the evidence comes from.

Scribe gives the model a tool for searching the knowledge base. The model must use that tool and then answer from the returned context. The application controls authentication, retrieval, storage, and citations, and the model turns the retrieved evidence into an answer. This reduces hallucination risk compared with asking the model to answer from memory.

## Data flow summary

The upload-to-answer lifecycle is one chain:

1. A user uploads a document.
2. Scribe stores the original privately.
3. Scribe creates a Source record.
4. Scribe queues ingestion.
5. The worker extracts text.
6. The text is split into chunks.
7. Each chunk receives an embedding.
8. Chunks and embeddings are stored.
9. The Source becomes ready.
10. The user asks a question.
11. The question receives an embedding.
12. Vector search finds similar chunks.
13. Scribe builds evidence context.
14. The AI model writes an answer from that context.
15. The answer includes citations.
16. Scribe saves the chat, usage, trace, and evaluation.
17. The user can inspect the Sources and evidence.

## Mental model

Think of Scribe as an evidence machine. Documents enter as private files, and the system turns them into indexed evidence. Questions retrieve that evidence, the AI model explains it, and the interface lets the user inspect the path from document to answer.

The point of the project is a controlled, private, inspectable way to ask questions of your own documents, not chat for its own sake.
