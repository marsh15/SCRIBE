"use client";

import { useMemo, useState } from "react";
import Link from "next/link";
import { PLAN_CATALOG } from "@/lib/billing/plans";

const CURRENCY_SYMBOL: Record<"INR" | "USD", string> = {
  INR: "₹",
  USD: "$",
};

export default function PricingPage() {
  const [currency, setCurrency] = useState<"INR" | "USD">("INR");

  const plans = useMemo(() => Object.values(PLAN_CATALOG), []);

  return (
    <main className="min-h-screen bg-background text-foreground">
      <div className="mx-auto max-w-6xl px-6 py-12">
        <header className="flex flex-wrap items-center justify-between gap-3">
          <div>
            <p className="text-[11px] font-mono uppercase tracking-[0.16em] text-muted-foreground">Pricing</p>
            <h1 className="mt-2 font-serif text-4xl">Affordable plans for production RAG</h1>
          </div>
          <div className="inline-flex rounded-sm border border-border p-1">
            <button
              className={`px-3 py-1.5 text-xs font-mono uppercase tracking-wider rounded-sm ${
                currency === "INR" ? "bg-primary text-primary-foreground" : "text-muted-foreground"
              }`}
              onClick={() => setCurrency("INR")}
            >
              INR
            </button>
            <button
              className={`px-3 py-1.5 text-xs font-mono uppercase tracking-wider rounded-sm ${
                currency === "USD" ? "bg-primary text-primary-foreground" : "text-muted-foreground"
              }`}
              onClick={() => setCurrency("USD")}
            >
              USD
            </button>
          </div>
        </header>

        <div className="mt-10 grid gap-4 md:grid-cols-3">
          {plans.map((plan) => {
            const price = plan.monthlyPrice[currency];
            return (
              <article
                key={plan.code}
                className={`rounded-sm border p-5 ${
                  plan.code === "pro" ? "border-[#00C4A0]/50 bg-card" : "border-border bg-card"
                }`}
              >
                <p className="font-mono text-[11px] uppercase tracking-widest text-muted-foreground">{plan.name}</p>
                <h2 className="mt-3 text-4xl font-serif">
                  {CURRENCY_SYMBOL[currency]}
                  {price}
                  <span className="text-sm font-mono text-muted-foreground">/month</span>
                </h2>
                <p className="mt-3 text-sm text-muted-foreground">{plan.description}</p>

                <ul className="mt-4 space-y-2 text-sm">
                  <li>Max file size: {plan.limits.maxFileSizeMb} MB</li>
                  <li>Storage: {plan.limits.storageGb} GB</li>
                  <li>Input tokens: {plan.limits.includedModelInputTokens.toLocaleString()}</li>
                  <li>Output tokens: {plan.limits.includedModelOutputTokens.toLocaleString()}</li>
                  <li>Embedding tokens: {plan.limits.includedEmbeddingTokens.toLocaleString()}</li>
                  <li>{plan.limits.allowOverage ? "Auto overage enabled" : "No overage (hard cap)"}</li>
                </ul>

                <Link
                  href={`/sign-in?redirect_url=/settings/billing?plan=${plan.code}&currency=${currency}`}
                  className="mt-5 inline-flex w-full items-center justify-center rounded-sm bg-primary px-3 py-2 text-xs font-mono uppercase tracking-wider text-primary-foreground"
                >
                  {plan.code === "free" ? "Start Free" : `Choose ${plan.name}`}
                </Link>
              </article>
            );
          })}
        </div>

        <p className="mt-8 text-xs font-mono text-muted-foreground uppercase tracking-wider">
          Overage defaults: input ₹0.06/1K, output ₹0.50/1K, embedding ₹0.04/1K, storage ₹20/GB-month.
        </p>
      </div>
    </main>
  );
}
