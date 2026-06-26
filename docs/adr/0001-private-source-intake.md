# ADR-0001: Private Blob uploads with server-owned Source intake

## Status

Accepted

## Context

Scribe previously selected among server upload, browser extraction, and queued ingestion based on file size. Source state, quota checks, retries, persistence, and metering consequently leaked across the upload page and several request modules. Large browser-ingested files also lost their original document.

## Decision

All new Sources upload directly to private Vercel Blob with a constrained client token. A signed Blob completion callback queues one ingestion job. A bearer-authenticated Vercel cron invokes the Source intake module once per minute. The module owns Source state, retry, indexing, metering, and deletion behavior.

Legacy batch endpoints remain temporarily available for already-processing Sources without a Blob URL, but the product no longer creates such Sources.

## Consequences

- Originals remain available for previews and citations.
- Production uploads require Vercel Blob and a Pro-compatible cron schedule.
- Source intake can be exercised through one interface and one state model.
- The queue may add roughly one minute before processing begins.
