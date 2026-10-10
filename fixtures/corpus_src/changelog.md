# Northwind Dispatch Platform Changelog

## v4.3.1 (2026-09-02)

- ⟦Fixed label rendering for A6 thermal printers (INV-2061).⟧

## v4.3.0 (2026-07-21)

- ⟦Added bulk label printing, up to 250 labels per request.⟧
- Added the delivered_at field to webhook payloads.

## v4.2.1 (2026-04-09)

- ⟦Fixed duplicate webhook delivery (INV-2047).⟧

## v4.2.0 (2026-02-11)

- ⟦Raised the API rate limit from 300 to 600 requests per minute.⟧
- Dropped support for TLS 1.1.

## v4.1.0 (2025-12-02)

- ⟦ERR_AUTH_403 is now returned instead of ERR_AUTH_401 for revoked API keys.⟧

## v4.0.0 (2025-09-15)

- ⟦Launched API v2; the v1 API will be switched off on 2026-06-30.⟧
