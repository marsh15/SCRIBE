"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { AlertTriangle, FileWarning, Loader2 } from "lucide-react";
import { Dialog, DialogClose, DialogContent, DialogDescription, DialogTitle, DialogTrigger } from "@/components/ui/dialog";
import { bboxToViewportRect, type ViewportRect } from "@/lib/evidence/geometry";
import { citationAccessibleName, planOpen, type OpenPlan } from "@/lib/evidence/open-plan";
import { findCsvRowRange, findQuoteInTextItems, splitAroundQuote, type TextRange } from "@/lib/evidence/quote-match";
import type { EvidenceAvailable, EvidenceOpen } from "@/lib/evidence/types";

/** Highlight: contrast >= 3:1 against the page in both themes, plus an outline so it is never colour-only. */
const MARK = "rounded-sm bg-amber-300 text-black outline outline-2 outline-offset-1 outline-black dark:outline-white";
const BOX = "absolute rounded-sm bg-amber-300/40 outline outline-2 outline-amber-600 dark:outline-amber-300";

function prefersReducedMotion() {
  return typeof window !== "undefined" && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
}
const scrollBehavior = (): ScrollBehavior => (prefersReducedMotion() ? "auto" : "smooth");

type Props = {
  itemId: string;
  label: string;
  /** Known before opening (from the answer): used for the screen-reader name only. */
  sourceName?: string;
  page?: number | null;
  children?: React.ReactNode;
  className?: string;
};

/** Keyboard-operable citation link. Every open re-fetches /api/evidence/[id], so access is re-checked each time. */
export function CitationLink({ itemId, label, sourceName, page, children, className }: Props) {
  const [open, setOpen] = useState(false);
  const name = sourceName ? citationAccessibleName(sourceName, page, label) : `Open citation ${label}`;
  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogTrigger asChild>
        <button
          type="button"
          aria-label={name}
          aria-haspopup="dialog"
          className={
            className ??
            "inline-flex min-h-11 min-w-11 items-center justify-center rounded-sm px-2 text-sm font-medium underline underline-offset-2 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ring"
          }
        >
          {children ?? `[${label}]`}
        </button>
      </DialogTrigger>
      {open && <EvidenceViewerBody itemId={itemId} />}
    </Dialog>
  );
}

type Load =
  | { state: "loading" }
  | { state: "error"; message: string }
  | { state: "notFound" }
  | { state: "ready"; evidence: EvidenceOpen };

function EvidenceViewerBody({ itemId }: { itemId: string }) {
  const [load, setLoad] = useState<Load>({ state: "loading" });
  const titleRef = useRef<HTMLHeadingElement>(null);

  useEffect(() => {
    const ctl = new AbortController();
    fetch(`/api/evidence/${itemId}`, { cache: "no-store", signal: ctl.signal })
      .then(async (res) => {
        if (res.status === 404) return setLoad({ state: "notFound" });
        if (!res.ok) return setLoad({ state: "error", message: "The citation could not be opened right now. Please retry." });
        setLoad({ state: "ready", evidence: (await res.json()) as EvidenceOpen });
      })
      .catch((e) => {
        if (e?.name !== "AbortError") setLoad({ state: "error", message: "The citation could not be opened right now. Please retry." });
      });
    return () => ctl.abort();
  }, [itemId]);

  return (
    <DialogContent
      showCloseButton={false}
      className="max-h-[90vh] overflow-y-auto sm:max-w-3xl"
      // Focus moves to the title (a labelled landmark for the dialog), not to a control behind a spinner.
      onOpenAutoFocus={(e) => {
        e.preventDefault();
        titleRef.current?.focus();
      }}
    >
      <DialogTitle ref={titleRef} tabIndex={-1} className="outline-none">
        {load.state === "ready" && !load.evidence.sourceDeleted
          ? `${load.evidence.sourceName}, ${planOpen(load.evidence).pageLabel}`
          : "Citation"}
      </DialogTitle>
      <DialogDescription className="sr-only">Source passage for this citation.</DialogDescription>
      <div aria-live="polite">
        {load.state === "loading" && (
          <p className="flex items-center gap-2 text-sm text-muted-foreground">
            <Loader2 className="size-4 animate-spin motion-reduce:animate-none" aria-hidden /> Opening citation…
          </p>
        )}
        {load.state === "notFound" && <p role="alert">This citation was not found.</p>}
        {load.state === "error" && <p role="alert">{load.message}</p>}
        {load.state === "ready" && load.evidence.sourceDeleted && (
          <p role="status" className="flex items-center gap-2">
            <FileWarning className="size-4" aria-hidden /> {load.evidence.notice}
          </p>
        )}
        {load.state === "ready" && !load.evidence.sourceDeleted && <EvidenceContent evidence={load.evidence} />}
      </div>
      <DialogClose className="min-h-11 justify-self-end rounded-md border px-4 text-sm focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ring">
        Close
      </DialogClose>
    </DialogContent>
  );
}

function EvidenceContent({ evidence }: { evidence: EvidenceAvailable }) {
  const plan = planOpen(evidence);
  return (
    <div className="grid gap-3">
      <p className="text-sm text-muted-foreground">
        {evidence.label} · {evidence.sourceName}
        {evidence.versionNumber !== null && ` · version ${evidence.versionNumber}`} · {plan.pageLabel}
        {evidence.headingPath.length > 0 && ` · ${evidence.headingPath.join(" › ")}`}
      </p>
      {plan.ocrNotice && (
        <p role="note" className="flex items-center gap-2 rounded-md border px-3 py-2 text-sm">
          <AlertTriangle className="size-4 shrink-0" aria-hidden /> {plan.ocrNotice}
        </p>
      )}
      <blockquote className="border-l-2 pl-3 text-sm whitespace-pre-wrap">
        <mark className={MARK}>{evidence.quote}</mark>
      </blockquote>
      {plan.format === "pdf" ? (
        plan.page === null ? (
          <p className="text-sm" role="status">Page unavailable: the quoted passage above is shown without a page preview.</p>
        ) : (
          <PdfPassage evidence={evidence} plan={plan} page={plan.page} />
        )
      ) : (
        <TextPassage evidence={evidence} plan={plan} />
      )}
    </div>
  );
}

// ---------------------------------------------------------------- text, markdown, CSV, DOCX

const WINDOW = 1500;

function TextPassage({ evidence, plan }: { evidence: EvidenceAvailable; plan: OpenPlan }) {
  const [text, setText] = useState<string | null>(null);
  const [failed, setFailed] = useState(false);
  const markRef = useRef<HTMLElement>(null);
  const previewable = plan.format === "md" || plan.format === "txt" || plan.format === "csv";

  useEffect(() => {
    if (!previewable) return;
    const ctl = new AbortController();
    fetch(`/api/files/${evidence.sourceId}/view`, { signal: ctl.signal })
      .then((r) => (r.ok ? r.text() : Promise.reject(new Error("view"))))
      .then(setText)
      .catch((e) => e?.name !== "AbortError" && setFailed(true));
    return () => ctl.abort();
  }, [evidence.sourceId, previewable]);

  useEffect(() => {
    markRef.current?.scrollIntoView({ block: "center", behavior: scrollBehavior() });
  }, [text]);

  if (!previewable) {
    return <p className="text-sm text-muted-foreground">A rendered preview is not available for this file type; the cited passage is shown above.</p>;
  }
  if (failed) return <p role="alert" className="text-sm">The original file could not be loaded.</p>;
  if (text === null) return <p className="text-sm text-muted-foreground">Loading the original…</p>;

  let parts: [string, string, string] | null = null;
  if (plan.highlight.kind === "csv-row") {
    const r: TextRange | null = findCsvRowRange(text, plan.highlight.rowNumber);
    if (r) parts = [text.slice(0, r.start), text.slice(r.start, r.end), text.slice(r.end)];
  }
  parts ??= splitAroundQuote(text, evidence.quote);
  if (!parts) {
    return <p className="text-sm" role="status">The passage was not found in the stored file (it may have changed since it was cited).</p>;
  }
  const [before, hit, after] = parts;
  return (
    <pre
      tabIndex={0}
      aria-label={`${evidence.sourceName}, highlighted passage in context`}
      className="max-h-80 overflow-auto rounded-md border p-3 font-mono text-xs whitespace-pre-wrap focus-visible:outline-2 focus-visible:outline-ring"
    >
      {before.length > WINDOW ? `…${before.slice(-WINDOW)}` : before}
      <mark ref={markRef} className={MARK}>{hit}</mark>
      {after.length > WINDOW ? `${after.slice(0, WINDOW)}…` : after}
    </pre>
  );
}

// ---------------------------------------------------------------- PDF (pdf.js, already a dependency)

type PdfState =
  | { state: "loading" }
  | { state: "error" }
  | { state: "ready"; width: number; height: number; rects: ViewportRect[]; located: boolean };

function PdfPassage({ evidence, plan, page }: { evidence: EvidenceAvailable; plan: OpenPlan; page: number }) {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const wrapRef = useRef<HTMLDivElement>(null);
  const firstRect = useRef<HTMLDivElement>(null);
  const [pdf, setPdf] = useState<PdfState>({ state: "loading" });

  const draw = useCallback(async () => {
    const pdfjs = await import("pdfjs-dist");
    pdfjs.GlobalWorkerOptions.workerSrc = new URL("pdfjs-dist/build/pdf.worker.min.mjs", import.meta.url).toString();
    const task = pdfjs.getDocument({ url: `/api/files/${evidence.sourceId}/view` });
    const doc = await task.promise;
    try {
      if (page > doc.numPages) return setPdf({ state: "error" });
      const pg = await doc.getPage(page);
      const base = pg.getViewport({ scale: 1 });
      const avail = Math.min(wrapRef.current?.clientWidth ?? 700, 900);
      const scale = avail / base.width;
      const viewport = pg.getViewport({ scale });
      const canvas = canvasRef.current;
      if (!canvas) return;
      canvas.width = Math.floor(viewport.width);
      canvas.height = Math.floor(viewport.height);
      await pg.render({ canvasContext: canvas.getContext("2d")!, canvas, viewport }).promise;

      let rects: ViewportRect[] = [];
      if (plan.highlight.kind === "bbox") {
        // Boxes are top-left-origin points in the upright page frame; scale == px per point.
        const geometry = { pageWidth: base.width, pageHeight: base.height, scale };
        rects = plan.highlight.boxes.map((b) => bboxToViewportRect(b, geometry)).filter((r): r is ViewportRect => r !== null);
      } else {
        // No bbox (OCR or missing geometry): match the quote against the page's text layer.
        const content = await pg.getTextContent();
        const items = content.items.filter((i): i is typeof i & { str: string } => "str" in i);
        rects = findQuoteInTextItems(items, evidence.quote).map((i) => {
          const it = items[i] as unknown as { transform: number[]; width: number; height: number };
          const [x1, y1, x2, y2] = viewport.convertToViewportRectangle([
            it.transform[4], it.transform[5], it.transform[4] + it.width, it.transform[5] + (it.height || Math.abs(it.transform[3])),
          ]);
          return { left: Math.min(x1, x2), top: Math.min(y1, y2), width: Math.abs(x2 - x1), height: Math.abs(y2 - y1) };
        });
      }
      setPdf({ state: "ready", width: viewport.width, height: viewport.height, rects, located: rects.length > 0 });
    } finally {
      void doc.destroy();
    }
  }, [evidence.quote, evidence.sourceId, page, plan.highlight]);

  useEffect(() => {
    let live = true;
    draw().catch(() => live && setPdf({ state: "error" }));
    return () => {
      live = false;
    };
  }, [draw]);

  useEffect(() => {
    if (pdf.state === "ready") firstRect.current?.scrollIntoView({ block: "center", behavior: scrollBehavior() });
  }, [pdf]);

  return (
    <div ref={wrapRef} className="grid gap-2">
      {pdf.state === "loading" && <p className="text-sm text-muted-foreground">Loading page {page}…</p>}
      {pdf.state === "error" && (
        <p role="alert" className="text-sm">The original PDF page could not be shown. The cited passage is above.</p>
      )}
      <div
        className="relative max-h-96 overflow-auto rounded-md border"
        role="img"
        aria-label={`${evidence.sourceName}, page ${page}${pdf.state === "ready" && pdf.located ? ", cited passage highlighted" : ""}`}
        hidden={pdf.state === "error"}
      >
        <div className="relative" style={pdf.state === "ready" ? { width: pdf.width, height: pdf.height } : undefined}>
          <canvas ref={canvasRef} className="block" />
          {pdf.state === "ready" &&
            pdf.rects.map((r, i) => (
              <div key={i} ref={i === 0 ? firstRect : undefined} className={BOX} style={{ left: r.left, top: r.top, width: r.width, height: r.height }} aria-hidden />
            ))}
        </div>
      </div>
      {pdf.state === "ready" && !pdf.located && (
        <p role="status" className="text-sm">The passage could not be located on this page; use the quoted text above.</p>
      )}
    </div>
  );
}
