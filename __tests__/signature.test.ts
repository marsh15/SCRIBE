import { describe, it, expect } from "vitest";
import crypto from "node:crypto";

// ---- Inline the functions under test (avoid DB/env dependencies in unit tests) ----

interface UploadTokenPayload {
  userId: string;
  fileName: string;
  fileSize: number;
  fileType: string;
  expiresAt: number;
}

const TEST_SECRET = "test-signing-secret-for-unit-tests";

function createUploadToken(payload: UploadTokenPayload) {
  const rawPayload = Buffer.from(JSON.stringify(payload)).toString("base64url");
  const signature = crypto
    .createHmac("sha256", TEST_SECRET)
    .update(rawPayload)
    .digest("base64url");
  return `${rawPayload}.${signature}`;
}

function verifyUploadToken(token: string): UploadTokenPayload | null {
  const [rawPayload, signature] = token.split(".");
  if (!rawPayload || !signature) return null;

  const expected = crypto
    .createHmac("sha256", TEST_SECRET)
    .update(rawPayload)
    .digest("base64url");

  try {
    if (!crypto.timingSafeEqual(Buffer.from(signature), Buffer.from(expected))) {
      return null;
    }
  } catch {
    return null;
  }

  const payload = JSON.parse(
    Buffer.from(rawPayload, "base64url").toString("utf-8")
  ) as UploadTokenPayload;

  if (Date.now() > payload.expiresAt) return null;
  return payload;
}

// ---- Tests ----

describe("Upload token sign/verify roundtrip", () => {
  const validPayload: UploadTokenPayload = {
    userId: "user_123",
    fileName: "test.pdf",
    fileSize: 1024,
    fileType: "application/pdf",
    expiresAt: Date.now() + 10 * 60 * 1000, // 10 minutes from now
  };

  it("should verify a freshly created token successfully", () => {
    const token = createUploadToken(validPayload);
    const result = verifyUploadToken(token);
    expect(result).not.toBeNull();
    expect(result?.userId).toBe("user_123");
    expect(result?.fileName).toBe("test.pdf");
  });

  it("should return null for an expired token", () => {
    const expiredPayload: UploadTokenPayload = {
      ...validPayload,
      expiresAt: Date.now() - 1000, // 1 second in the past
    };
    const token = createUploadToken(expiredPayload);
    const result = verifyUploadToken(token);
    expect(result).toBeNull();
  });

  it("should return null for a tampered signature", () => {
    const token = createUploadToken(validPayload);
    const [rawPayload] = token.split(".");
    const tamperedToken = `${rawPayload}.invalidsignature`;
    const result = verifyUploadToken(tamperedToken);
    expect(result).toBeNull();
  });

  it("should return null for a tampered payload", () => {
    const token = createUploadToken(validPayload);
    const [, signature] = token.split(".");
    // Modify the payload to a different userId
    const maliciousPayload = Buffer.from(
      JSON.stringify({ ...validPayload, userId: "user_attacker" })
    ).toString("base64url");
    const tamperedToken = `${maliciousPayload}.${signature}`;
    const result = verifyUploadToken(tamperedToken);
    expect(result).toBeNull();
  });

  it("should return null for a malformed token (no separator)", () => {
    expect(verifyUploadToken("notavalidtoken")).toBeNull();
    expect(verifyUploadToken("")).toBeNull();
  });
});
