import { notFound } from "next/navigation";
import { isAdmin } from "@/lib/auth";
import { MigrationDashboard } from "@/app/admin/migration/dashboard";

export const dynamic = "force-dynamic";

export default async function AdminMigrationPage() {
  if (!(await isAdmin())) notFound();
  return <MigrationDashboard />;
}
