-- Migration: Make user_id NOT NULL on files and chats tables
-- This closes the data isolation gap where files/chats without an owner
-- could result in orphaned embeddings accessible to any user.

-- Step 1: Backfill any existing null user_ids with a placeholder (if any exist)
-- Note: In production, review these rows and assign real user IDs before applying.
UPDATE files SET user_id = 'unknown-backfill' WHERE user_id IS NULL;
UPDATE chats SET user_id = 'unknown-backfill' WHERE user_id IS NULL;

-- Step 2: Add NOT NULL constraint
ALTER TABLE "files" ALTER COLUMN "user_id" SET NOT NULL;
ALTER TABLE "chats" ALTER COLUMN "user_id" SET NOT NULL;
