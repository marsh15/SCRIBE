# Scribe domain context

## Source

An original document a user adds to Scribe together with its extracted text, indexed chunks, and evidence metadata. A Source remains user-owned and private throughout its lifecycle.

## Source intake

The lifecycle that reserves a private upload, stores the original, extracts and chunks its text, generates embeddings, and makes the Source available as evidence.

## Source status

- `uploading`: the private upload was reserved but has not completed.
- `queued`: the original is stored and awaiting intake.
- `processing`: extraction, chunking, or embedding is running.
- `retrying`: an intake attempt failed and another attempt is scheduled.
- `ready`: the Source can be retrieved and cited.
- `failed`: intake or upload reached a terminal failure.
