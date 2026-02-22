CREATE TABLE "documents" (
	"id" serial PRIMARY KEY NOT NULL,
	"content" text NOT NULL,
	"embeddings" vector(1536) NOT NULL
);
--> statement-breakpoint
CREATE INDEX "embeddings_index" ON "documents" USING hnsw ("embeddings" vector_cosine_ops);