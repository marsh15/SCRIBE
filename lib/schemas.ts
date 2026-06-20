import { z } from "zod";
export const fileStatusSchema = z.enum(["queued", "processing", "ready", "failed", "deleting"]);
export type FileStatus = z.infer<typeof fileStatusSchema>;
export const citationSchema = z.object({
  id: z.string().min(1), fileId: z.string().min(1), fileName: z.string().min(1),
  chunkId: z.string().min(1), excerpt: z.string().min(1),
  page: z.number().int().positive().optional(), section: z.string().min(1).optional(),
  score: z.number().min(0).max(1).optional(),
});
export type Citation = z.infer<typeof citationSchema>;
export const groundedResponseSchema = z.object({
  answer: z.string(),
  grounding: z.enum(["grounded", "partial", "unavailable", "retrieval_failed"]),
  citations: z.array(citationSchema),
});
export type GroundedResponse = z.infer<typeof groundedResponseSchema>;
export const apiErrorSchema = z.object({ error: z.object({ code: z.string(), message: z.string(), requestId: z.string().optional() }) });
export type ApiError = z.infer<typeof apiErrorSchema>;
