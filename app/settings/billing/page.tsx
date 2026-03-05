"use client";

import { useEffect, useMemo, useState } from "react";
import Script from "next/script";
import { PLAN_CATALOG, type PlanCode } from "@/lib/billing/plans";

type UsageResponse = {
  ok: boolean;
  planCode: PlanCode;
  usage: {
    modelInputTokens: number;
    modelOutputTokens: number;
    embeddingTokens: number;
    storageGbDay: number;
  };
  included: {
    modelInputTokens: number;
    modelOutputTokens: number;
    embeddingTokens: number;
    storageGb: number;
  };
  projectedOverageInr: number;
};

type RazorpayOrderResponse = {
  ok: boolean;
  gateway: "razorpay";
  order: {
    orderId: string;
    amount: number;
    currency: string;
    keyId: string;
    name: string;
    description: string;
    prefill: {
      name?: string;
      email?: string;
    };
  };
};

declare global {
  interface Window {
    Razorpay: new (options: Record<string, unknown>) => {
      open: () => void;
      on: (event: string, handler: (response: Record<string, unknown>) => void) => void;
    };
  }
}

function Meter({ label, used, limit }: { label: string; used: number; limit: number }) {
  const percent = limit > 0 ? Math.min(100, Math.round((used / limit) * 100)) : 0;
  return (
    <div className="space-y-1">
      <div className="flex items-center justify-between text-xs font-mono uppercase tracking-wider text-muted-foreground">
        <span>{label}</span>
        <span>
          {used.toLocaleString()} / {limit.toLocaleString()}
        </span>
      </div>
      <div className="h-2 rounded-sm bg-muted overflow-hidden">
        <div
          className={`h-full ${percent > 90 ? "bg-destructive" : "bg-[#00C4A0]"}`}
          style={{ width: `${percent}%` }}
        />
      </div>
    </div>
  );
}

export default function BillingSettingsPage() {
  const [planCode, setPlanCode] = useState<PlanCode>("pro");
  const [currency, setCurrency] = useState<"INR" | "USD">("INR");
  const [usage, setUsage] = useState<UsageResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [checkoutStatus, setCheckoutStatus] = useState<"idle" | "success" | "error">("idle");

  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    const queryPlan = params.get("plan");
    const queryCurrency = params.get("currency");

    if (queryPlan === "free" || queryPlan === "pro" || queryPlan === "team") {
      setPlanCode(queryPlan);
    }
    if (queryCurrency === "USD") {
      setCurrency("USD");
    }

    let mounted = true;
    fetch("/api/billing/usage")
      .then((res) => res.json())
      .then((json: UsageResponse) => {
        if (!mounted) return;
        if (json.ok) {
          setUsage(json);
          if (json.planCode) setPlanCode(json.planCode);
        }
      })
      .catch(() => { });

    return () => {
      mounted = false;
    };
  }, []);

  const selectedPlan = useMemo(() => PLAN_CATALOG[planCode], [planCode]);

  async function startCheckout(nextPlan: PlanCode) {
    if (nextPlan === "free") return;

    setLoading(true);
    setCheckoutStatus("idle");

    try {
      const payload = {
        planCode: nextPlan,
        gateway: "razorpay",
        currency,
        successUrl: `${window.location.origin}/settings/billing?checkout=success`,
        cancelUrl: `${window.location.origin}/settings/billing?checkout=cancel`,
      };

      const res = await fetch("/api/billing/checkout", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });

      const json = (await res.json()) as RazorpayOrderResponse;

      if (!res.ok || !json.order) {
        alert("Failed to create order. Please try again.");
        return;
      }

      const { order } = json;

      // Open Razorpay Standard Checkout modal
      const options = {
        key: order.keyId,
        amount: String(order.amount),
        currency: order.currency,
        name: order.name,
        description: order.description,
        order_id: order.orderId,
        prefill: order.prefill,
        theme: {
          color: "#00C4A0",
        },
        handler: async (response: {
          razorpay_payment_id: string;
          razorpay_order_id: string;
          razorpay_signature: string;
        }) => {
          // Verify payment server-side
          try {
            const verifyRes = await fetch("/api/billing/verify", {
              method: "POST",
              headers: { "Content-Type": "application/json" },
              body: JSON.stringify({
                razorpay_order_id: response.razorpay_order_id,
                razorpay_payment_id: response.razorpay_payment_id,
                razorpay_signature: response.razorpay_signature,
                planCode: nextPlan,
                gateway: "razorpay",
              }),
            });

            const verifyJson = (await verifyRes.json()) as { ok: boolean; verified?: boolean; error?: string };

            if (verifyRes.ok && verifyJson.verified) {
              setCheckoutStatus("success");
              setPlanCode(nextPlan);
              // Refresh usage after successful upgrade
              const usageRes = await fetch("/api/billing/usage");
              const usageJson = (await usageRes.json()) as UsageResponse;
              setUsage(usageJson);
            } else {
              setCheckoutStatus("error");
            }
          } catch {
            setCheckoutStatus("error");
          }
        },
        modal: {
          ondismiss: () => {
            setLoading(false);
          },
        },
      };

      const rzp = new window.Razorpay(options);
      rzp.open();
    } catch {
      alert("Something went wrong. Please try again.");
    } finally {
      setLoading(false);
    }
  }

  async function openPortal() {
    const res = await fetch("/api/billing/portal", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ gateway: "razorpay", returnUrl: window.location.href }),
    });
    const json = (await res.json()) as { url?: string; error?: string };
    if (!res.ok || !json.url) {
      alert(json.error || "Failed to open billing portal");
      return;
    }
    window.location.href = json.url;
  }

  return (
    <main className="min-h-screen bg-background text-foreground">
      {/* Load Razorpay Checkout.js */}
      <Script src="https://checkout.razorpay.com/v1/checkout.js" strategy="lazyOnload" />

      <div className="mx-auto max-w-5xl px-6 py-10">
        <h1 className="font-serif text-4xl">Billing & Usage</h1>
        <p className="mt-2 text-sm text-muted-foreground">
          Track current cycle usage and manage subscriptions.
        </p>

        {/* Success/Error banners */}
        {checkoutStatus === "success" && (
          <div className="mt-4 rounded-sm border border-[#00C4A0]/50 bg-[#00C4A0]/10 p-4 text-sm text-[#00C4A0]">
            ✅ Payment successful! Your plan has been upgraded.
          </div>
        )}
        {checkoutStatus === "error" && (
          <div className="mt-4 rounded-sm border border-destructive/50 bg-destructive/10 p-4 text-sm text-destructive">
            ❌ Payment verification failed. Please contact support.
          </div>
        )}

        <section className="mt-6 rounded-sm border border-border bg-card p-5 space-y-4">
          <div className="flex flex-wrap items-center gap-3 justify-between">
            <div>
              <p className="text-xs font-mono uppercase tracking-wider text-muted-foreground">Current Plan</p>
              <h2 className="font-serif text-3xl mt-1">{selectedPlan.name}</h2>
            </div>
            <div className="flex gap-2">
              <select
                value={currency}
                onChange={(e) => setCurrency(e.target.value as "INR" | "USD")}
                className="border border-border rounded-sm bg-background px-2 py-1.5 text-xs font-mono uppercase tracking-wider"
              >
                <option value="INR">INR</option>
                <option value="USD">USD</option>
              </select>
              <button
                onClick={openPortal}
                className="rounded-sm border border-border px-3 py-1.5 text-xs font-mono uppercase tracking-wider hover:bg-muted"
              >
                Manage Billing
              </button>
            </div>
          </div>

          {usage ? (
            <div className="space-y-3">
              <Meter
                label="Model Input Tokens"
                used={usage.usage.modelInputTokens}
                limit={usage.included.modelInputTokens}
              />
              <Meter
                label="Model Output Tokens"
                used={usage.usage.modelOutputTokens}
                limit={usage.included.modelOutputTokens}
              />
              <Meter
                label="Embedding Tokens"
                used={usage.usage.embeddingTokens}
                limit={usage.included.embeddingTokens}
              />
              <p className="text-xs font-mono uppercase tracking-wider text-muted-foreground">
                Projected overage: ₹{usage.projectedOverageInr.toFixed(2)}
              </p>
            </div>
          ) : (
            <p className="text-sm text-muted-foreground">Loading usage...</p>
          )}
        </section>

        <section className="mt-6 grid gap-3 md:grid-cols-3">
          {(["free", "pro", "team"] as PlanCode[]).map((code) => {
            const plan = PLAN_CATALOG[code];
            return (
              <article key={code} className="rounded-sm border border-border bg-card p-4">
                <p className="font-mono text-[11px] uppercase tracking-widest text-muted-foreground">
                  {plan.name}
                </p>
                <p className="mt-2 text-2xl font-serif">
                  {currency === "INR" ? "₹" : "$"}
                  {plan.monthlyPrice[currency]}
                  <span className="text-xs font-mono text-muted-foreground"> / month</span>
                </p>
                <button
                  onClick={() => startCheckout(code)}
                  disabled={loading || code === "free"}
                  className="mt-4 w-full rounded-sm bg-primary text-primary-foreground px-3 py-2 text-xs font-mono uppercase tracking-wider disabled:opacity-50"
                >
                  {code === "free" ? "Current / Free" : `Choose ${plan.name}`}
                </button>
              </article>
            );
          })}
        </section>
      </div>
    </main>
  );
}
