# How Scribe Works

Scribe is an AI knowledge workspace. Its main purpose is simple: a user uploads private documents, asks questions about those documents, and receives answers that are tied back to evidence.

The project is not just a chatbot. It is a complete document-to-answer system. It stores source documents, extracts their text, divides that text into searchable pieces, turns those pieces into mathematical representations, retrieves the most relevant pieces for each question, and asks an AI model to answer only from that retrieved evidence.

This document explains the project conceptually, without code.

## The Big Picture

Scribe has three major responsibilities:

1. Source intake

   Scribe accepts documents from a user, stores the originals privately, extracts text from them, splits the text into chunks, and prepares those chunks for search.

2. Evidence-based chat

   When the user asks a question, Scribe searches the user's own indexed documents, builds a context package from the best matches, and gives that context to the AI assistant.

3. Workspace management

   Scribe keeps each user's files, chats, billing usage, ingestion state, and retrieval traces separate and visible.

The product idea is evidence-first. Answers are not treated as free-floating AI text. A good Scribe answer should show where it came from, which document supported it, and why the system believed that content was relevant.

## Main Product Concept

Scribe is built around three everyday user actions:

1. Add sources

   The user uploads documents such as PDFs, text files, Markdown files, CSV files, or DOCX files. These become the user's private knowledge base.

2. Ask questions

   The user asks natural-language questions in chat. The chat does not answer from general memory first. It searches the user's uploaded documents first.

3. Inspect evidence

   The user can open citations, view retrieved chunks, inspect the retrieval pipeline, and check whether the answer is grounded in real source material.

This is why the interface is organized around Sources, Conversation, and Evidence. Those are the three conceptual pillars of the application.

## Core Domain Ideas

### Source

A Source is an original document uploaded by a user. It includes the original file, its extracted text, its indexed chunks, and metadata used for citations.

A Source is private to the user who uploaded it. Other users should not be able to search it, preview it, cite it, or delete it.

### Source Intake

Source intake is the lifecycle that turns an uploaded file into searchable evidence.

It includes:

- reserving a private upload
- storing the original document
- marking the Source as queued
- extracting text
- splitting the text into chunks
- generating embeddings
- saving searchable chunks
- marking the Source as ready

The important idea is that uploading a document and making it searchable are separate steps. Uploading stores the file. Intake makes it useful to the RAG system.

### Source Status

Every Source has a visible state. The main states are:

- uploading: the upload was reserved but has not finished
- queued: the file is stored and waiting to be processed
- processing: extraction, chunking, or embedding is happening
- retrying: a previous attempt failed, but the system will try again
- ready: the Source can be searched and cited
- failed: processing could not be completed

This status model matters because document processing can take time, fail, or be retried. Instead of hiding that complexity, Scribe makes it legible to the user.

### Chunk

A chunk is a section of extracted document text. Large documents are too big to search or send to an AI model all at once, so Scribe breaks them into smaller pieces.

Chunks preserve useful metadata, such as approximate page, section, order, and character position. That metadata helps the product show citations and document locations.

### Embedding

An embedding is a numeric representation of text. Text with similar meaning should produce embeddings that are close to each other in vector space.

Scribe creates embeddings for document chunks and for each user question. This allows the system to compare a question against stored document meaning, not just exact words.

### Vector Search

Vector search is the retrieval step. Scribe compares the embedding of the user's question with the embeddings of document chunks. The most similar chunks are selected as evidence.

The result is a ranked set of document passages. These passages are the material the AI model is expected to use when answering.

### RAG

RAG means Retrieval-Augmented Generation.

In Scribe, the flow is:

1. Retrieve relevant chunks from the user's knowledge base.
2. Augment the AI prompt with those chunks.
3. Generate an answer grounded in that evidence.

The theory behind RAG is that an AI model should not need to memorize a user's private documents. Instead, the app retrieves the right evidence at the moment of the question and gives it to the model as context.

## How Uploading Works

The upload process is designed around privacy and reliability.

First, the user chooses a file. Scribe checks whether the user is allowed to upload it based on file size, plan limits, and supported file type.

Next, Scribe reserves a private upload location. The browser receives a constrained upload token, which means the browser can upload only the intended file under the intended conditions.

After the file lands in private storage, Scribe records that the upload completed and creates a queued ingestion job. At this point, the file exists as a Source, but it may not be searchable yet.

Then the ingestion worker processes queued jobs. It downloads the private original, extracts text, chunks it, embeds the chunks, stores the results, and marks the Source ready.

This design keeps original documents available for preview and citation while allowing heavy processing to happen separately from the initial upload.

## How Ingestion Works

Ingestion is the transformation from file to knowledge.

Conceptually, ingestion has five stages:

1. Load the original

   The system reads the uploaded file from private storage. Some older legacy flows may read from stored base64 data, but the intended design is private object storage.

2. Extract text

   Different document types require different extraction strategies. PDFs are parsed as PDF text. DOCX files are converted to raw text. CSV files are flattened into text rows. Plain text and Markdown are read directly.

3. Split text into chunks

   The extracted text is divided into overlapping sections. Overlap helps preserve context across chunk boundaries. A sentence or idea that spans two chunks is less likely to be lost.

4. Generate embeddings

   Each chunk is sent to the embedding model, producing a vector representation.

5. Save the index

   Scribe stores the chunk text, metadata, and embedding in the database. Once that is complete, the Source becomes ready for retrieval.

If ingestion fails, Scribe records the error. Some failures are retryable. For example, temporary provider or infrastructure problems can be retried, while unsupported or empty documents usually become terminal failures.

## Why There Is a Queue

Document processing is slower and more fragile than normal page requests. A large document may need extraction, chunking, embedding, database writes, and usage metering. Those steps should not all depend on one browser request staying open.

The queue gives Scribe a controlled background workflow.

The worker can claim jobs, process them, retry them, and recover jobs that got stuck during a server timeout or deployment interruption. This is the reason the product can show states such as queued, processing, retrying, and failed.

On Vercel, this queue is driven by a protected internal route that is normally called by a scheduled job. There is also a "process now" path so a queued Source can be manually advanced from the UI.

## How Chat Works

The chat experience is centered on one rule: every user message should search the knowledge base before the assistant answers.

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

The assistant is instructed not to guess. If the retrieved documents do not contain enough support, the answer should say that the information was not found in the user's documents.

## How Citations Work

A citation is a bridge between an AI answer and a Source.

When search returns chunks, Scribe formats each chunk with source information: file name, file link, chunk rank, estimated page or section, and content. The assistant can then refer to those exact sources in the answer.

The citation links point back into the document viewer. This allows the user to move from answer to evidence, rather than simply trusting the generated response.

The project treats citations as part of the answer, not decoration. That matches the product principle that evidence should be visible and inspectable.

## How the Evidence Inspector Works

The Evidence inspector is the right-side view that shows what happened during retrieval.

It represents the RAG pipeline as visible stages:

- tokenize query
- embed query
- vector search
- build context
- stream AI response

It also shows operational details such as latency, number of RAG calls, retrieved chunks, similarity scores, and evaluation results when available.

This is useful because RAG systems can fail silently. A normal chatbot might produce a confident answer even when retrieval was poor. Scribe exposes retrieval behavior so the user can inspect whether the answer had meaningful support.

## RAG Observability

Scribe records retrieval traces. A trace connects a user question, the retrieved chunks, the generated answer, and timing information.

The trace data answers questions such as:

- What query was used for retrieval?
- Which chunks were retrieved?
- Which files did those chunks come from?
- How similar were the chunks to the query?
- How long did embedding, retrieval, and generation take?
- Which assistant answer used this evidence?

The project also includes an evaluation concept. After an answer is generated, Scribe can judge whether the answer was grounded, relevant, and citation-supported. This is a quality-control layer for the RAG system.

## User Isolation

Scribe is a multi-user product. User isolation is essential.

The authenticated user's ID is attached to Sources, chats, usage records, traces, and billing data. Search is scoped to the current user, so one user's question can retrieve only that user's documents.

This tenant boundary is one of the most important security and product rules in the system. Without it, private knowledge bases would leak across accounts.

## Database Role

The database acts as the system of record.

It stores:

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

The database is not just a passive store. Because it supports vector data, it is also part of the retrieval engine. The same database that tracks files and chats also powers similarity search over document embeddings.

## Storage Role

Private object storage holds the original uploaded documents.

The database stores metadata and extracted/indexed text, while object storage keeps the original file available. This separation matters because original files can be large, binary, and not ideal to store directly inside relational tables.

Keeping the original allows Scribe to support document previews, source deletion, reprocessing, and stronger citation behavior.

## Authentication Role

Authentication identifies the current user and protects private routes.

Most important operations depend on the current user:

- listing Sources
- uploading a Source
- deleting a Source
- viewing a document
- creating a chat
- loading chat history
- searching documents
- recording usage
- managing billing

Authentication is therefore not just a login feature. It is the boundary that makes the knowledge base private.

## Billing and Usage

Scribe includes SaaS billing concepts.

Plans define limits such as maximum file size, storage allowance, included model input tokens, included model output tokens, included embedding tokens, and whether overage is allowed.

Usage events are recorded for activities such as:

- model input tokens
- model output tokens
- embedding input tokens
- storage usage estimates

The project can calculate whether a user is within included usage or projected to exceed it. For free plans, overage is not allowed, so chat can be blocked when quota is exhausted. Paid plans can allow metered overage.

Billing is feature-flagged, which means the core product can run with billing disabled during development or portfolio use.

## Payment System

The project is prepared for Razorpay-backed subscriptions.

The payment layer handles customer records, subscriptions, plan identifiers, payment events, and webhook signature verification. Webhook verification matters because payment events should be trusted only when they really came from the provider.

Conceptually, payment events update the user's commercial state, while usage events measure how much of the product the user consumed.

## Feature Flags

Feature flags let the same project run in different modes.

The main flags control whether billing is enabled, whether async ingestion is enabled, and whether the public landing page is enabled.

This gives the app flexibility. For example, a development environment may keep billing off while still testing uploads and chat. A production environment may enable commercial flows and scheduled ingestion.

## Main User Interfaces

### Landing Page

The public page introduces Scribe and its value: upload documents, ask questions, and inspect cited answers.

### Upload and Sources Page

This is where users add documents and manage the Source library. It shows upload progress, intake state, failures, retry actions, and deletion.

The page also polls for pending ingestion states, so users can see queued or processing documents become ready.

### Chat Page

This is where users ask questions. A new chat can be created from an initial prompt. Existing chats load their saved message history.

The chat UI streams assistant responses and shows when evidence retrieval is active.

### Document Viewer

The document viewer lets users inspect an uploaded Source. Depending on the file type and availability of the original, it can preview the original document or show extracted text and chunks.

This page is important because citations should be inspectable. The user can move from an answer back to the supporting Source.

### Evidence Inspector

The Evidence inspector explains the retrieval process behind the answer. It is the diagnostic surface for RAG.

### Billing and Pricing Pages

These pages expose plans, usage, checkout, and billing management when billing is enabled.

### Admin Analytics

The admin analytics surface exists to inspect product or system activity at a higher level.

## Why the Three-Pane Layout Matters

The product is not designed like a simple single-column chatbot. It uses a three-pane mental model:

- left area: navigation and workspace context
- center area: active task, such as upload, chat, or document viewing
- right area: evidence and retrieval diagnostics

This reflects the product's core promise. The user should be able to ask a question and inspect the evidence without losing context.

## Error Handling Philosophy

Scribe tries to make failures understandable.

Upload and ingestion errors are translated into user-facing states. A scanned PDF that contains no extractable text should not fail mysteriously. A missing API key should produce a configuration message. A queued job that times out should be recoverable.

The general philosophy is that uncertainty should be visible. If the system cannot process a file or cannot find evidence, it should say so plainly.

## Reliability Concepts

The project uses several reliability patterns:

- queued background work for ingestion
- retry scheduling for transient failures
- stale job recovery after timeouts
- idempotent usage records for some events
- private upload completion callbacks
- database retry wrappers around fragile operations
- explicit Source statuses
- protected internal ingestion endpoint

These patterns matter because AI and document workflows often depend on external services. The app must survive provider errors, network failures, function timeouts, duplicate callbacks, and partial completion.

## Security and Privacy Concepts

Scribe's security model is based on a few practical rules:

- users must be authenticated for private operations
- every Source belongs to one user
- search is scoped to the current user
- uploaded originals are stored privately
- upload tokens are constrained
- cron ingestion requires a bearer secret
- payment webhooks are signature-checked
- deletion is owner-scoped

The most important privacy promise is that a user's documents are not globally searchable. They are part of that user's private workspace.

## AI Model Responsibilities

The AI model is responsible for language generation, but not for deciding what documents the user owns or where the evidence comes from.

Scribe gives the model a tool for searching the knowledge base. The model must use that tool, then answer from the returned context.

This division of responsibility is important:

- the application controls authentication, retrieval, storage, and citations
- the model turns retrieved evidence into a helpful answer

This reduces hallucination risk compared with asking the model to answer from memory.

## Data Flow Summary

The upload-to-answer lifecycle can be understood as one chain:

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

This is the entire project in one lifecycle.

## Mental Model

The best way to understand Scribe is as an evidence machine.

Documents enter as private files. The system turns them into indexed evidence. Questions retrieve that evidence. The AI model explains the evidence. The interface lets the user inspect the path from document to answer.

The value of the project is not merely that it can chat. The value is that it gives users a controlled, private, inspectable way to ask questions of their own documents.
