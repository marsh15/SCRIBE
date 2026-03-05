import crypto from "node:crypto";

interface UploadTokenPayload {
  userId: string;
  fileName: string;
  fileSize: number;
  fileType: string;
  expiresAt: number;
}

function signingSecret() {
  return process.env.UPLOAD_SIGNING_SECRET ?? process.env.CLERK_SECRET_KEY ?? "dev-upload-secret";
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

  const payload = JSON.parse(Buffer.from(rawPayload, "base64url").toString("utf-8")) as UploadTokenPayload;

  if (Date.now() > payload.expiresAt) return null;
  return payload;
}
