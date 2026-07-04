import { generateText } from "ai";
import { google } from "@ai-sdk/google";
import { z } from "zod";
import type { InsertRagEvaluation } from "@/lib/db-schema";
import type { RagToolChunk } from "@/lib/rag-types";

const judgeResponseSchema = z.object({
  groundednessScore: z.number(),
  answerRelevanceScore: z.number(),
  citationSupportScore: z.number(),
  overallScore: z.number(),
  verdict: z.enum(["pass", "partial", "fail"]),
  rationale: z.string().min(1),
});

export type ParsedJudgeResponse = z.infer<typeof judgeResponseSchema>;

export const RAG_JUDGE_MODEL =
  process.env.GOOGLE_RAG_JUDGE_MODEL ||
  process.env.GOOGLE_CHAT_MODEL ||
  "gemini-2.5-flash";

function clampScore(score: number) {
  if (!Number.isFinite(score)) return 0;
  return Math.min(1, Math.max(0, score));
}

function extractJson(text: string) {
  const trimmed = text.trim();
  const fenced = trimmed.match(/```(?:json)?\s*([\s\S]*?)\s*```/i);
  if (fenced?.[1]) return fenced[1].trim();

  const firstBrace = trimmed.indexOf("{");
  const lastBrace = trimmed.lastIndexOf("}");
  if (firstBrace >= 0 && lastBrace > firstBrace) {
    return trimmed.slice(firstBrace, lastBrace + 1);
  }

  return trimmed;
}

export function parseJudgeResponse(text: string): ParsedJudgeResponse {
  const parsed = judgeResponseSchema.parse(JSON.parse(extractJson(text)));

  return {
    groundednessScore: clampScore(parsed.groundednessScore),
    answerRelevanceScore: clampScore(parsed.answerRelevanceScore),
    citationSupportScore: clampScore(parsed.citationSupportScore),
    overallScore: clampScore(parsed.overallScore),
    verdict: parsed.verdict,
    rationale: parsed.rationale,
  };
}

export function buildEvaluationRecord(input: {
  traceId: string;
  judgeModel: string;
  parsed: ParsedJudgeResponse;
}): InsertRagEvaluation {
  return {
    traceId: input.traceId,
    status: "completed",
    judgeModel: input.judgeModel,
    groundednessScore: input.parsed.groundednessScore,
    answerRelevanceScore: input.parsed.answerRelevanceScore,
    citationSupportScore: input.parsed.citationSupportScore,
    overallScore: input.parsed.overallScore,
    verdict: input.parsed.verdict,
    rationale: input.parsed.rationale,
    error: null,
  };
}

export function buildEvaluationUnavailableRecord(input: {
  traceId: string;
  judgeModel?: string;
  status: "disabled" | "failed";
  error: string;
}): InsertRagEvaluation {
  return {
    traceId: input.traceId,
    status: input.status,
    judgeModel: input.judgeModel ?? RAG_JUDGE_MODEL,
    groundednessScore: null,
    answerRelevanceScore: null,
    citationSupportScore: null,
    overallScore: null,
    verdict: null,
    rationale: null,
    error: input.error,
  };
}

function formatChunksForJudge(chunks: RagToolChunk[]) {
  return chunks
    .map(
      (chunk) =>
        `[${chunk.rank}] ${chunk.fileName} (chunk ${chunk.chunkIndex + 1}, similarity ${chunk.similarity.toFixed(3)})\n${chunk.content}`
    )
    .join("\n\n---\n\n");
}

export async function evaluateRagAnswer(input: {
  traceId: string;
  question: string;
  answer: string;
  chunks: RagToolChunk[];
}): Promise<InsertRagEvaluation> {
  if (process.env.ENABLE_RAG_EVALS !== "true") {
    return buildEvaluationUnavailableRecord({
      traceId: input.traceId,
      status: "disabled",
      error: "RAG evals are disabled. Set ENABLE_RAG_EVALS=true to enable judge scoring.",
    });
  }

  if (!process.env.GOOGLE_GENERATIVE_AI_API_KEY?.trim()) {
    return buildEvaluationUnavailableRecord({
      traceId: input.traceId,
      status: "failed",
      error: "Google Generative AI API key is not configured.",
    });
  }

  try {
    const result = await generateText({
      model: google(RAG_JUDGE_MODEL),
      system:
        "You are a strict RAG quality evaluator. Return only valid JSON. Do not include markdown.",
      prompt: `Evaluate whether the answer is supported by the retrieved chunks.

Question:
${input.question}

Retrieved chunks:
${formatChunksForJudge(input.chunks)}

Answer:
${input.answer}

Return strict JSON with exactly these fields:
{
  "groundednessScore": 0-1,
  "answerRelevanceScore": 0-1,
  "citationSupportScore": 0-1,
  "overallScore": 0-1,
  "verdict": "pass" | "partial" | "fail",
  "rationale": "brief explanation"
}`,
    });

    return buildEvaluationRecord({
      traceId: input.traceId,
      judgeModel: RAG_JUDGE_MODEL,
      parsed: parseJudgeResponse(result.text),
    });
  } catch (error) {
    return buildEvaluationUnavailableRecord({
      traceId: input.traceId,
      judgeModel: RAG_JUDGE_MODEL,
      status: "failed",
      error: error instanceof Error ? error.message : "Judge evaluation failed.",
    });
  }
}
