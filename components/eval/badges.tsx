import { AlertTriangle, CheckCircle2, FlaskConical, Trophy } from "lucide-react";
import type { EvalRun } from "@/lib/eval/model";

/** Run status: text plus icon, never colour alone. A non-measurement is loud; a measurement is quiet. */
export function RunStamp({ run }: { run: Pick<EvalRun, "measurement" | "seeded" | "stamp"> }) {
  if (run.measurement) {
    return (
      <span className="inline-flex items-center gap-1 rounded-sm border border-border px-1.5 py-0.5 font-mono text-[10px] uppercase tracking-wider text-muted-foreground">
        <CheckCircle2 className="size-3" aria-hidden="true" />
        Measurement
      </span>
    );
  }
  const label = run.seeded ? "SEEDED TEST DATA - NOT A MEASUREMENT" : (run.stamp ?? "FAKE - NOT A MEASUREMENT");
  return (
    <span
      className="inline-flex items-center gap-1 rounded-sm border border-destructive bg-destructive/10 px-1.5 py-0.5 font-mono text-[10px] font-semibold uppercase tracking-wider text-destructive"
      data-testid="run-stamp"
    >
      {run.seeded ? <FlaskConical className="size-3" aria-hidden="true" /> : <AlertTriangle className="size-3" aria-hidden="true" />}
      {label}
    </span>
  );
}

export function BestBadge() {
  return (
    <span className="inline-flex items-center gap-1 rounded-sm border border-rag px-1.5 py-0.5 font-mono text-[10px] uppercase tracking-wider text-rag">
      <Trophy className="size-3" aria-hidden="true" />
      Best measured run
    </span>
  );
}

export function shortId(id: string): string {
  return id.slice(0, 8);
}
export function shortHash(h: string | null): string {
  return h ? h.slice(0, 10) : "n/a";
}
export function shortCommit(c: string | null): string {
  return c && c !== "unknown" ? c.slice(0, 9) : "unknown";
}
export function formatWhen(iso: string | null): string {
  return iso ? iso.slice(0, 16).replace("T", " ") + " UTC" : "n/a";
}
export function modelList(ids: Record<string, string>): string {
  const e = Object.entries(ids);
  return e.length ? e.map(([k, v]) => `${k}: ${v}`).join(", ") : "none recorded";
}
