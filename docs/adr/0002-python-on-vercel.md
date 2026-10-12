# ADR-0002: Python service runs inside the existing Vercel deployment

## Status

Accepted

## Context

Scribe needs Python for parsing, retrieval and evaluation. A separate worker host was rejected: the project must stay on Vercel Hobby only.

## Decision

FastAPI runs as a Vercel Python function behind a `/api/py/*` rewrite, with a package in `intelligence/`. T02 verifies co-deployment first; if it fails, a second Vercel project rooted at `intelligence/` is used. Calls are authenticated with 60-second HS256 internal tokens minted by Next.js (aud `scribe-intel`).

## Consequences

Heavy local ML (Docling, PyTorch rerankers) is excluded by the 500 MB bundle limit. Functions are capped at 300 s, so long work must be resumable (ADR-0010).
