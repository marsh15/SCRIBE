// Uses Google's Generative Language REST API (v1beta) directly.
// Model: gemini-embedding-001 — outputs 3072-dimensional vectors.
// Free tier: 100 requests/minute. Vercel-compatible (pure HTTP fetch).

const GOOGLE_API_KEY = process.env.GOOGLE_GENERATIVE_AI_API_KEY!;
const MODEL = "models/gemini-embedding-2-preview";
const BASE_URL = `https://generativelanguage.googleapis.com/v1beta/${MODEL}`;

const sanitizeInput = (text: string) => text.replace(/\s+/g, " ").trim();
const sleep = (ms: number) => new Promise((resolve) => setTimeout(resolve, ms));

async function fetchWithRetry(url: string, options: RequestInit, maxRetries = 5): Promise<Response> {
    for (let attempt = 0; attempt <= maxRetries; attempt++) {
        const res = await fetch(url, options);

        if (res.ok) return res;

        // Rate limited — backoff and retry
        if (res.status === 429) {
            const body = await res.text();
            // Try to extract retry delay from response
            const retryMatch = body.match(/retryDelay.*?(\d+)s/);
            const waitSec = retryMatch ? parseInt(retryMatch[1]) + 2 : (attempt + 1) * 15;
            console.log(`[Embeddings] Rate limited — retrying in ${waitSec}s (attempt ${attempt + 1}/${maxRetries})`);
            await sleep(waitSec * 1000);
            continue;
        }

        // Non-retryable error
        const body = await res.text();
        throw new Error(`Google Embedding API error ${res.status}: ${body}`);
    }
    throw new Error("Max retries exceeded for embedding API");
}

async function batchEmbedMany(texts: string[]): Promise<number[][]> {
    const res = await fetchWithRetry(
        `${BASE_URL}:batchEmbedContents?key=${GOOGLE_API_KEY}`,
        {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
                requests: texts.map((text) => ({
                    model: MODEL,
                    content: { parts: [{ text }] },
                })),
            }),
        }
    );

    const data = await res.json();
    return (data.embeddings as { values: number[] }[]).map((e) => e.values);
}

export async function generateEmbedding(text: string): Promise<number[]> {
    const res = await fetchWithRetry(
        `${BASE_URL}:embedContent?key=${GOOGLE_API_KEY}`,
        {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
                model: MODEL,
                content: { parts: [{ text: sanitizeInput(text) }] },
            }),
        }
    );

    const data = await res.json();
    return data.embedding.values as number[];
}

export async function generateEmbeddings(texts: string[]): Promise<number[][]> {
    const inputs = texts.map(sanitizeInput);

    // Gemini Free Tier restricts to 15 Requests Per Minute.
    // WARNING: `batchEmbedContents` counts each *string* in the batch as 1 request!
    // So a batch of 15 strings instantly maxes out the 1-minute quota.
    // To prevent 429 errors that crash Vercel, we send 14 chunks and wait a full minute.
    const BATCH_SIZE = 14;
    const allEmbeddings: number[][] = [];

    for (let i = 0; i < inputs.length; i += BATCH_SIZE) {
        const batch = inputs.slice(i, i + BATCH_SIZE);
        const batchNum = Math.floor(i / BATCH_SIZE) + 1;
        const totalBatches = Math.ceil(inputs.length / BATCH_SIZE);
        console.log(
            `[Embeddings] Batch ${batchNum}/${totalBatches}: chunks ${i + 1}–${i + batch.length} of ${inputs.length}`
        );

        const embeddings = await batchEmbedMany(batch);
        allEmbeddings.push(...embeddings);

        // Sleep 61 seconds to refresh the 15 RPM quota completely.
        // NOTE: This means a 200-chunk book will take ~15 minutes to embed.
        // Vercel serverless functions will timeout (max 60s). This script is designed
        // to be run locally or via a long-running background worker for huge files.
        if (i + BATCH_SIZE < inputs.length) {
            console.log(`[Embeddings] Sleeping 61s to respect 15 RPM limit...`);
            await sleep(61000);
        }
    }

    return allEmbeddings;
}