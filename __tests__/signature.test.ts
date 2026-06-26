import { afterEach, describe, expect, it, vi } from "vitest";

afterEach(() => {
  vi.unstubAllEnvs();
  vi.resetModules();
});

async function signatureModule() {
  vi.stubEnv("UPLOAD_SIGNING_SECRET", "test-signing-secret-for-unit-tests");
  return import("@/lib/uploads/signature");
}

describe("upload token production interface", () => {
  const validPayload = {
    userId: "user_123",
    fileName: "test.pdf",
    fileSize: 1024,
    fileType: "application/pdf",
    expiresAt: Date.now() + 10 * 60 * 1000,
  };

  it("verifies a freshly created token", async () => {
    const { createUploadToken, verifyUploadToken } = await signatureModule();
    expect(verifyUploadToken(createUploadToken(validPayload))).toEqual(validPayload);
  });

  it("rejects expired, malformed, and tampered tokens", async () => {
    const { createUploadToken, verifyUploadToken } = await signatureModule();
    const expired = createUploadToken({ ...validPayload, expiresAt: Date.now() - 1 });
    expect(verifyUploadToken(expired)).toBeNull();
    expect(verifyUploadToken("not-a-token")).toBeNull();

    const token = createUploadToken(validPayload);
    const [payload] = token.split(".");
    expect(verifyUploadToken(`${payload}.tampered`)).toBeNull();
  });
});
