import { describe, it, expect } from "vitest";
import {
  isBillingPortalAvailable,
  resolveBillingPortalUrl,
} from "@/lib/billing/portal";
import {
  canOpenOriginalPdfFile,
  getMissingOriginalFileMessage,
} from "@/lib/files/preview";

describe("Billing portal availability", () => {
  it("is unavailable when no hosted billing URL is configured", () => {
    expect(isBillingPortalAvailable(undefined)).toBe(false);
    expect(resolveBillingPortalUrl(undefined)).toBeNull();
  });

  it("uses the configured hosted billing URL when present", () => {
    const url = "https://example.com/manage-billing";
    expect(isBillingPortalAvailable(url)).toBe(true);
    expect(resolveBillingPortalUrl(url)).toBe(url);
  });
});

describe("File preview availability", () => {
  it("hides open-pdf actions when the original binary is unavailable", () => {
    expect(
      canOpenOriginalPdfFile({
        isPdf: true,
        hasFileData: false,
        hasStorageUrl: false,
      })
    ).toBe(false);
  });

  it("shows open-pdf actions when storage-backed originals exist", () => {
    expect(
      canOpenOriginalPdfFile({
        isPdf: true,
        hasFileData: false,
        hasStorageUrl: true,
      })
    ).toBe(true);
  });

  it("explains that extracted text and chunks remain available", () => {
    expect(getMissingOriginalFileMessage("notes.pdf")).toContain("extracted text");
    expect(getMissingOriginalFileMessage("notes.pdf")).toContain("indexed chunks");
  });
});
