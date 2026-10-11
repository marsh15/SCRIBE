import { STATUS_META, type ClaimStatus, type VerificationData, type VerifyPhase } from "@/lib/evidence/verification";

const TONE: Record<ClaimStatus, string> = {
  VERIFIED: "border-emerald-600/60 text-emerald-700 dark:text-emerald-400",
  PARTIAL: "border-amber-600/60 text-amber-700 dark:text-amber-400",
  UNSUPPORTED: "border-red-600/60 text-red-700 dark:text-red-400",
  UNCITED: "border-dashed border-muted-foreground/60 text-muted-foreground",
  UNVERIFIED: "border-dotted border-muted-foreground/60 text-muted-foreground",
  INVALID_CITATION: "border-red-600/60 text-red-700 dark:text-red-400",
};

/** Per-claim status. Text plus icon plus border style, never colour alone. */
export function ClaimBadge({ status }: { status: ClaimStatus }) {
  const meta = STATUS_META[status];
  return (
    <span
      data-testid="claim-badge"
      data-status={status}
      title={meta.description}
      className={`ml-1 inline-flex items-center gap-0.5 whitespace-nowrap rounded-sm border px-1 align-baseline text-[10px] font-mono uppercase tracking-wide ${TONE[status]}`}
    >
      <span aria-hidden="true">{meta.icon}</span>
      {meta.label}
    </span>
  );
}

export function verificationSummary(data: VerificationData | undefined): string {
  if (!data) return "";
  if (data.state === "verifying") return "Verifying citations…";
  if (data.state === "unavailable") return "Verification unavailable";
  if (data.claims.length === 0) return "";
  const counts = new Map<ClaimStatus, number>();
  for (const c of data.claims) counts.set(c.status, (counts.get(c.status) ?? 0) + 1);
  const parts = [...counts].map(([s, n]) => `${n} ${STATUS_META[s].label.toLowerCase()}`);
  return `Verification complete: ${parts.join(", ")}`;
}

/** Live region: announced politely when verification starts and when it completes. Always mounted so screen readers see the change. */
export function VerificationStatus({ phase, data }: { phase: VerifyPhase; data: VerificationData | undefined }) {
  const text = phase === "final" || phase === "unavailable" || phase === "verifying" ? verificationSummary(data) : "";
  return (
    <div
      role="status"
      aria-live="polite"
      data-testid="verification-status"
      data-phase={phase}
      className={text ? "mt-2 font-mono text-[10px] uppercase tracking-widest text-muted-foreground" : "sr-only"}
    >
      {text}
    </div>
  );
}
