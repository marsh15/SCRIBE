# ADR-0010: Ingestion as leased, resumable steps

## Status

Accepted

## Context

Hobby caps functions at 300 s and cron at once a day.

## Decision

Ingestion is PARSE → CHUNK → EMBED → ACTIVATE. Each invocation claims a job with a lease token, writes progress only while it holds the lease, and re-fires itself while work remains. Activation is a single transaction. Retries follow a recoverable/permanent taxonomy.

## Consequences

Stale workers cannot corrupt the index. Daily cron is only a recovery sweep.
