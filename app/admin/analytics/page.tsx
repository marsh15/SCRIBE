import { sql } from "drizzle-orm";
import { db } from "@/lib/db-config";
import { chats, files, ingestionJobs, subscriptions } from "@/lib/db-schema";

export const dynamic = "force-dynamic";

async function getMetrics() {
  const [activeSubs] = await db
    .select({ count: sql<number>`count(*)::int` })
    .from(subscriptions)
    .where(sql`${subscriptions.status} = 'active'`);

  const [totalUsers] = await db
    .select({ count: sql<number>`count(distinct ${files.userId})::int` })
    .from(files);

  const [totalFiles] = await db
    .select({ count: sql<number>`count(*)::int` })
    .from(files);

  const [failedIngestions] = await db
    .select({ count: sql<number>`count(*)::int` })
    .from(ingestionJobs)
    .where(sql`${ingestionJobs.status} = 'failed'`);

  const [totalChats] = await db
    .select({ count: sql<number>`count(*)::int` })
    .from(chats);

  return {
    activeSubs: activeSubs?.count ?? 0,
    totalUsers: totalUsers?.count ?? 0,
    totalFiles: totalFiles?.count ?? 0,
    failedIngestions: failedIngestions?.count ?? 0,
    totalChats: totalChats?.count ?? 0,
  };
}

export default async function AdminAnalyticsPage() {
  const metrics = await getMetrics();

  return (
    <main className="min-h-screen bg-background text-foreground">
      <div className="mx-auto max-w-5xl px-6 py-10">
        <h1 className="font-serif text-4xl">Admin Analytics</h1>
        <p className="mt-2 text-sm text-muted-foreground">Core launch metrics for billing and ingestion health.</p>

        <div className="mt-8 grid gap-4 sm:grid-cols-2 lg:grid-cols-5">
          <div className="rounded-sm border border-border bg-card p-4">
            <p className="text-[10px] font-mono uppercase tracking-wider text-muted-foreground">Active Subs</p>
            <p className="mt-2 text-3xl font-serif">{metrics.activeSubs}</p>
          </div>
          <div className="rounded-sm border border-border bg-card p-4">
            <p className="text-[10px] font-mono uppercase tracking-wider text-muted-foreground">Active Users</p>
            <p className="mt-2 text-3xl font-serif">{metrics.totalUsers}</p>
          </div>
          <div className="rounded-sm border border-border bg-card p-4">
            <p className="text-[10px] font-mono uppercase tracking-wider text-muted-foreground">Files</p>
            <p className="mt-2 text-3xl font-serif">{metrics.totalFiles}</p>
          </div>
          <div className="rounded-sm border border-border bg-card p-4">
            <p className="text-[10px] font-mono uppercase tracking-wider text-muted-foreground">Failed Ingest</p>
            <p className="mt-2 text-3xl font-serif">{metrics.failedIngestions}</p>
          </div>
          <div className="rounded-sm border border-border bg-card p-4">
            <p className="text-[10px] font-mono uppercase tracking-wider text-muted-foreground">Chats</p>
            <p className="mt-2 text-3xl font-serif">{metrics.totalChats}</p>
          </div>
        </div>
      </div>
    </main>
  );
}
