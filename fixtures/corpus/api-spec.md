# Northwind Dispatch API v2 Specification

## 1. Overview

The base URL is https://api.northwind-logistics.example/v2. All requests use HTTPS and JSON.

### 1.1 Authentication

Send an API key as a bearer token in the `Authorization` header. Revoked API keys return the error code ERR_AUTH_403. Missing or malformed keys return `ERR_AUTH_401`.

### 1.2 Rate limits

The rate limit is 600 requests per minute per API key. Exceeding the limit returns `ERR_RATE_429` with a `Retry-After` header.

## 2. Endpoints

| Method | Path | Description |
|---|---|---|
| GET | /shipments/{id} | Fetch a shipment by id |
| POST | /shipments | Create a shipment |
| POST | /shipments/{id}/labels | Print a label (up to 250 labels per request) |
| DELETE | /shipments/{id} | Cancel a shipment that is not yet picked up |

### 2.1 Pagination

List endpoints return 50 items by default and at most 200 items per page.

### 2.2 Idempotency

An Idempotency-Key header is honoured for 24 hours. Repeating a request with the same key returns the original response.

## 3. Webhooks

Failed webhook deliveries are retried 5 times with exponential backoff starting at 30 seconds. Duplicate webhook deliveries were fixed in platform release v4.2.1 (ticket INV-2047).

Example payload:

```json
{
  "event": "shipment.delivered",
  "shipment_id": "NW0123456789",
  "delivered_at": "2026-09-14T10:32:00Z"
}
```

## 4. Error codes

| Code | HTTP status | Meaning |
|---|---|---|
| ERR_AUTH_401 | 401 | Missing or malformed API key |
| ERR_AUTH_403 | 403 | API key revoked or lacks scope |
| ERR_SHIP_404 | 404 | Shipment not found |
| ERR_VAL_422 | 422 | Request body failed validation |
| ERR_RATE_429 | 429 | Rate limit exceeded |

## 5. Known issues

- INV-2051: label PDFs for Austin shipments render the address in a wrong font.
- INV-2047 is closed.

## 6. Tracking numbers

Tracking numbers have the format NW followed by 10 digits.
