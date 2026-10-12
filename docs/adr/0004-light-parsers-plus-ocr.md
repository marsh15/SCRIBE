# ADR-0004: Light parsers plus Gemini OCR

## Status

Accepted

## Context

Docling needs 2 to 4 GB RAM and local models, which do not fit Vercel Python functions.

## Decision

pdfplumber, python-docx and stdlib csv parse documents into typed blocks. PDF pages with fewer than 25 characters and an image are rendered with pypdfium2 and transcribed by Gemini 2.5 Flash, bounded per step. PyMuPDF is excluded for its AGPL license.

## Consequences

OCR blocks carry no bounding box and are labelled as OCR. Table structure from OCR is out of scope. Docling may return later as an offline comparison.
