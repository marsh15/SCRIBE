import { describe, expect, it } from "vitest";
import {
  buildEvaluationRecord,
  buildEvaluationUnavailableRecord,
  parseJudgeResponse,
} from "@/lib/rag-eval";

describe("RAG judge response parsing", () => {
  it("parses strict JSON and clamps out-of-range scores", () => {
    const parsed = parseJudgeResponse(
      JSON.stringify({
        groundednessScore: 1.2,
        answerRelevanceScore: 0.75,
        citationSupportScore: -0.2,
        overallScore: 0.9,
        verdict: "partial",
        rationale: "Mostly grounded but one citation is weak.",
      }),
    );

    expect(parsed).toEqual({
      groundednessScore: 1,
      answerRelevanceScore: 0.75,
      citationSupportScore: 0,
      overallScore: 0.9,
      verdict: "partial",
      rationale: "Mostly grounded but one citation is weak.",
    });
  });

  it("extracts JSON from a fenced response", () => {
    const parsed = parseJudgeResponse(`\`\`\`json
{
  "groundednessScore": 0.8,
  "answerRelevanceScore": 0.7,
  "citationSupportScore": 0.6,
  "overallScore": 0.7,
  "verdict": "pass",
  "rationale": "Supported."
}
\`\`\``);

    expect(parsed.verdict).toBe("pass");
    expect(parsed.overallScore).toBe(0.7);
  });

  it("rejects invalid or incomplete judge JSON", () => {
    expect(() => parseJudgeResponse("not json")).toThrow();
    expect(() =>
      parseJudgeResponse(
        JSON.stringify({
          groundednessScore: 0.8,
          verdict: "pass",
          rationale: "Missing fields.",
        }),
      ),
    ).toThrow();
  });
});

describe("RAG evaluation records", () => {
  it("builds completed and unavailable records", () => {
    const completed = buildEvaluationRecord({
      traceId: "trace-1",
      judgeModel: "gemini-test",
      parsed: {
        groundednessScore: 0.9,
        answerRelevanceScore: 0.8,
        citationSupportScore: 0.7,
        overallScore: 0.8,
        verdict: "pass",
        rationale: "Grounded.",
      },
    });

    expect(completed).toEqual(
      expect.objectContaining({
        traceId: "trace-1",
        status: "completed",
        verdict: "pass",
        error: null,
      }),
    );

    const failed = buildEvaluationUnavailableRecord({
      traceId: "trace-2",
      judgeModel: "gemini-test",
      status: "failed",
      error: "Provider failed.",
    });

    expect(failed).toEqual(
      expect.objectContaining({
        traceId: "trace-2",
        status: "failed",
        overallScore: null,
        error: "Provider failed.",
      }),
    );
  });
});
