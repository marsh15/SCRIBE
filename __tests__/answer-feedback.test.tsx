// @vitest-environment jsdom

import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { AnswerFeedback } from "@/components/answer-feedback";

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

let host: HTMLDivElement;
let root: Root;
beforeEach(() => {
  host = document.createElement("div");
  document.body.appendChild(host);
  root = createRoot(host);
});
afterEach(() => {
  act(() => root.unmount());
  host.remove();
  vi.unstubAllGlobals();
});

it("posts a rating, selected reason, and optional comment for the persisted answer id", async () => {
  const fetchMock = vi.fn(async (_input: RequestInfo | URL, _init?: RequestInit) => new Response(JSON.stringify({ ok: true }), { status: 200 }));
  vi.stubGlobal("fetch", fetchMock);
  await act(async () => root.render(<AnswerFeedback messageId="stored-answer-id" />));
  await act(async () => host.querySelector<HTMLButtonElement>('button[aria-label="Unhelpful answer"]')!.click());
  const reason = [...host.querySelectorAll("button")].find((button) => button.textContent === "Wrong citation")!;
  await act(async () => reason.click());
  const textarea = host.querySelector("textarea")!;
  await act(async () => {
    Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, "value")!.set!.call(textarea, "The passage does not support this.");
    textarea.dispatchEvent(new Event("input", { bubbles: true }));
  });
  const submit = [...host.querySelectorAll("button")].find((button) => button.textContent === "Send feedback")!;
  await act(async () => submit.click());

  expect(fetchMock).toHaveBeenCalledTimes(1);
  expect(fetchMock.mock.calls[0][0]).toBe("/api/feedback");
  expect(JSON.parse(String(fetchMock.mock.calls[0][1]?.body))).toEqual({
    messageId: "stored-answer-id",
    rating: "down",
    reasons: ["wrong_citation"],
    comment: "The passage does not support this.",
  });
  expect(host.textContent).toContain("Thanks for your feedback.");
});
