# ADR-0007: Personal workspaces enforced with Postgres RLS

## Status

Accepted

## Context

Isolation currently depends on every query remembering a `user_id` filter.

## Decision

Each user gets one personal workspace; sources, chats and traces carry `workspace_id`. RLS policies compare `workspace_id` to `app.workspace_id`. The app connects as `scribe_app` (NOBYPASSRLS); queue claims go through SECURITY DEFINER functions. No invites or roles UI in P0.

## Consequences

A missed WHERE clause no longer leaks data. Billing stays keyed by user.
