import crypto from "node:crypto";
import { requireEnv } from "@/lib/env";

interface UploadTokenPayload {
  userId: string;
  fileName: string;
  fileSize: number;
  fileType: string;
  expiresAt: number;
}

function signingSecret() {
  return requireEnv("UPLOAD_SIGNING_SECRET");
}

export function createUploadToken(payload: UploadTokenPayload) {
  const rawPayload = Buffer.from(JSON.stringify(payload)).toString("base64url");
  const signature = crypto
    .createHmac("sha256", signingSecret())
    .update(rawPayload)
    .digest("base64url");

  return `${rawPayload}.${signature}`;
}

export function verifyUploadToken(token: string): UploadTokenPayload | null {
  const [rawPayload, signature] = token.split(".");
  if (!rawPayload || !signature) return null;

  const expected = crypto
    .createHmac("sha256", signingSecret())
    .update(rawPayload)
    .digest("base64url");

  try {
    if (!crypto.timingSafeEqual(Buffer.from(signature), Buffer.from(expected))) {
      return null;
    }
  } catch {
    return null;
  }

  try {
    const payload = JSON.parse(Buffer.from(rawPayload, "base64url").toString("utf-8")) as Partial<UploadTokenPayload>;
    if (
      typeof payload.userId !== "string" || typeof payload.fileName !== "string" ||
      typeof payload.fileSize !== "number" || typeof payload.fileType !== "string" ||
      typeof payload.expiresAt !== "number" || Date.now() > payload.expiresAt
    ) return null;
    return payload as UploadTokenPayload;
  } catch {
    return null;
  }
}
