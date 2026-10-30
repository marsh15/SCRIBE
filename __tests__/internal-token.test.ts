import { decodeJwt, jwtVerify, SignJWT } from "jose";
import { describe, expect, it, vi } from "vitest";

import { mintInternalToken } from "@/lib/internal-token";

const secret = "test-secret-test-secret-test-secret-32b";
const key = new TextEncoder().encode(secret);

describe("mintInternalToken", () => {
  it("sets sub, wid, scope, aud, HS256 and a 60 s expiry", async () => {
    const token = await mintInternalToken({ sub: "user_1", wid: "ws_1", scope: "health" }, secret);
    const { payload, protectedHeader } = await jwtVerify(token, key, {
      audience: "scribe-intel",
    });
    expect(protectedHeader.alg).toBe("HS256");
    expect(payload.sub).toBe("user_1");
    expect(payload.wid).toBe("ws_1");
    expect(payload.scope).toBe("health");
    expect(payload.aud).toBe("scribe-intel");
    expect(payload.exp! - payload.iat!).toBe(60);
  });

  it("is rejected for a different audience or secret", async () => {
    const token = await mintInternalToken({ sub: "u", wid: "w", scope: "health" }, secret);
    await expect(jwtVerify(token, key, { audience: "other" })).rejects.toThrow();
    await expect(
      jwtVerify(token, new TextEncoder().encode("y".repeat(40)), { audience: "scribe-intel" }),
    ).rejects.toThrow();
  });

  it("keeps a 60-second lifetime when the clock advances while the claims are set", async () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2026-10-30T00:00:00Z"));
    const original = SignJWT.prototype.setIssuedAt;
    vi.spyOn(SignJWT.prototype, "setIssuedAt").mockImplementation(function (this: SignJWT, value?: Parameters<SignJWT["setIssuedAt"]>[0]) {
      const token = original.call(this, value);
      vi.advanceTimersByTime(1000);
      return token;
    });
    try {
      const claims = decodeJwt(await mintInternalToken({ sub: "u", wid: "w", scope: "health" }, secret));
      expect(claims.exp! - claims.iat!).toBe(60);
    } finally {
      vi.restoreAllMocks();
      vi.useRealTimers();
    }
  });
});
