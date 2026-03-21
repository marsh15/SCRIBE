// Uses Google's Generative Language REST API (v1beta) directly.
// Model: gemini-embedding-2-preview — outputs 3072-dimensional vectors.
// Paid tier: up to 1500 RPM. Vercel-compatible (pure HTTP fetch).
import { EMBEDDING_MODEL_ID } from "@/lib/embedding-config";

const GOOGLE_API_KEY = process.env.GOOGLE_GENERATIVE_AI_API_KEY!;
const MODEL = EMBEDDING_MODEL_ID;
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
        `${BASE_URL}:batchEmbedContents`,
        {
            method: "POST",
            headers: {
                "Content-Type": "application/json",
                // API key sent as header — NOT in URL query params to avoid server log exposure
                "x-goog-api-key": GOOGLE_API_KEY,
            },
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
        `${BASE_URL}:embedContent`,
        {
            method: "POST",
            headers: {
                "Content-Type": "application/json",
                // API key sent as header — NOT in URL query params to avoid server log exposure
                "x-goog-api-key": GOOGLE_API_KEY,
            },
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

    // Paid API tier supports up to 1500 RPM — batch 100 chunks at a time with no forced sleep.
    // fetchWithRetry handles transient 429s via exponential backoff automatically.
    // NOTE: If you are still on the FREE tier (15 RPM), reduce BATCH_SIZE to 14 and run
    // ingestion locally (a 200-chunk book would take ~15 min, exceeding Vercel's 60s limit).
    const BATCH_SIZE = 100;
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
    }

    return allEmbeddings;
}
