import { describe, expect, it } from "vitest";
import { chunkContent } from "@/lib/chunking";
import { razorpayEventId } from "@/lib/billing/webhooks";

describe("chunking production interface", () => {
  it("assigns increasing offsets when text repeats", async () => {
    const content = `${"Introduction. ".repeat(500)}Conclusion.`;
    const chunks = await chunkContent(content);
    const offsets = chunks.map((chunk) => chunk.metadata.charOffset ?? -1);

    expect(chunks.length).toBeGreaterThan(1);
    for (let index = 1; index < offsets.length; index++) {
      expect(offsets[index]).toBeGreaterThan(offsets[index - 1]);
    }
  });
});

describe("Razorpay webhook production interface", () => {
  it("is stable for replayed bodies and distinct for different events", () => {
    const first = JSON.stringify({ event: "subscription.activated", id: "sub_123" });
    const second = JSON.stringify({ event: "subscription.charged", id: "sub_123" });

    expect(razorpayEventId(first)).toBe(razorpayEventId(first));
    expect(razorpayEventId(first)).not.toBe(razorpayEventId(second));
    expect(razorpayEventId(first)).toMatch(/^[0-9a-f]{64}$/);
  });
});
