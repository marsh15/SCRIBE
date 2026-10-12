import { createUIMessageStream, type UIMessageChunk } from "ai";
import {
  VERIFICATION_PART,
  VERIFICATION_PART_ID,
  type VerificationData,
} from "@/lib/evidence/verification";

export type TurnFinish = { text: string; messageId?: string; skip?: boolean } | null;

/**
 * Python-engine chat stream (T26): the model's own stream (without its `finish` chunk), then a `data-verification`
 * part going `verifying` -> `done | unavailable` (same part id, so the client replaces it in place), then `finish`.
 * The answer text is therefore complete and visible before verification starts, and the response only ends after it.
 * Verification failures never fail the stream.
 */
export function streamWithVerification(opts: {
  modelStream: ReadableStream<UIMessageChunk>;
  /** Resolves once the answer is persisted (or the turn ended); null when there is nothing to verify. */
  finished: Promise<TurnFinish>;
  verify: (turn: NonNullable<TurnFinish>) => Promise<VerificationData>;
  onError?: (error: unknown) => string;
  graceMs?: number;
}) {
  return createUIMessageStream({
    execute: async ({ writer }) => {
      let modelDone!: () => void;
      const ended = new Promise<void>((r) => (modelDone = r));
      writer.merge(
        opts.modelStream.pipeThrough(
          new TransformStream<UIMessageChunk, UIMessageChunk>({
            transform: (chunk, c) => c.enqueue(chunk),
            flush: () => modelDone(),
          }),
        ),
      );
      await ended;
      const turn = await Promise.race([
        opts.finished,
        new Promise<null>((r) => setTimeout(() => r(null), opts.graceMs ?? 10_000)),
      ]);
      if (turn && !turn.skip && turn.text.trim()) {
        const write = (data: VerificationData) =>
          writer.write({ type: VERIFICATION_PART, id: VERIFICATION_PART_ID, data } as UIMessageChunk);
        write({ state: "verifying" });
        let result: VerificationData;
        try {
          result = await opts.verify(turn);
        } catch {
          result = { state: "unavailable" };
        }
        write(result);
      }
      writer.write({ type: "finish" } as UIMessageChunk);
    },
    onError: opts.onError,
  });
}
