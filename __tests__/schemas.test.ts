import { describe, expect, it } from "vitest";
import { fileStatusSchema } from "@/lib/schemas";

describe("shared schemas", () => {
  it("accepts retryable Source lifecycle statuses", () => {
    expect(fileStatusSchema.parse("uploading")).toBe("uploading");
    expect(fileStatusSchema.parse("retrying")).toBe("retrying");
  });
});
