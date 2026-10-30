import { createHash } from "node:crypto";
import { copyFileSync, existsSync, mkdtempSync, mkdirSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { resolve } from "node:path";
import { spawnSync } from "node:child_process";
import { afterEach, describe, expect, it } from "vitest";
import { migrationManifest } from "@/scripts/neon-rehearsal/check-journal.mjs";

const temporaryRoots: string[] = [];

function history() {
  const root = mkdtempSync(resolve(tmpdir(), "scribe-journal-"));
  temporaryRoots.push(root);
  mkdirSync(resolve(root, "drizzle/meta"), { recursive: true });
  const entries = Array.from({ length: 10 }, (_, idx) => ({
    idx, tag: `${String(idx).padStart(4, "0")}_migration`, when: 1000 + idx,
  }));
  for (const entry of entries) writeFileSync(resolve(root, `drizzle/${entry.tag}.sql`), `select ${entry.idx};\n`);
  const journal = { dialect: "postgresql", entries };
  const save = () => writeFileSync(resolve(root, "drizzle/meta/_journal.json"), JSON.stringify(journal));
  save();
  return { root, journal, save };
}

afterEach(() => {
  for (const root of temporaryRoots.splice(0)) rmSync(root, { recursive: true, force: true });
});

describe("migration rehearsal manifest", () => {
  it("validates the actual journal and hashes exact file bytes in migration order", () => {
    const manifest = migrationManifest(process.cwd());
    expect(manifest[0].tag).toBe("0008_workspaces");
    expect(manifest.at(-1)?.tag).toBe("0020_legacy_workspace_default");
    expect(manifest).toHaveLength(13);
    for (const entry of manifest) {
      expect(entry.hash).toBe(createHash("sha256").update(readFileSync(resolve("drizzle", `${entry.tag}.sql`))).digest("hex"));
    }
  });

  it("refuses missing SQL and extra unjournaled SQL", () => {
    const { root } = history();
    writeFileSync(resolve(root, "drizzle/0010_unjournaled.sql"), "select 1;");
    expect(() => migrationManifest(root)).toThrow("Unjournaled migration files");
    rmSync(resolve(root, "drizzle/0010_unjournaled.sql"));
    rmSync(resolve(root, "drizzle/0009_migration.sql"));
    expect(() => migrationManifest(root)).toThrow("Missing migration file");
  });

  it("refuses reordered entries, duplicate timestamps and unsafe timestamps", () => {
    const { root, journal, save } = history();
    [journal.entries[8], journal.entries[9]] = [journal.entries[9], journal.entries[8]];
    save();
    expect(() => migrationManifest(root)).toThrow("out-of-order");
    [journal.entries[8], journal.entries[9]] = [journal.entries[9], journal.entries[8]];
    journal.entries[9].when = journal.entries[8].when;
    save();
    expect(() => migrationManifest(root)).toThrow("timestamps must increase");
    journal.entries[9].when = Number.MAX_SAFE_INTEGER + 1;
    save();
    expect(() => migrationManifest(root)).toThrow("timestamps must increase");
  });

  it("refuses paths or SQL fragments in journal tags", () => {
    const { root, journal, save } = history();
    journal.entries[9].tag = "0009_../../escape';--";
    save();
    expect(() => migrationManifest(root)).toThrow("Invalid or out-of-order");
  });

  it("rejects an invalid journal before invoking psql", () => {
    const { root } = history();
    const scripts = resolve(root, "scripts/neon-rehearsal");
    mkdirSync(scripts, { recursive: true });
    for (const name of ["apply.sh", "check-journal.mjs"]) {
      copyFileSync(resolve("scripts/neon-rehearsal", name), resolve(scripts, name));
    }
    rmSync(resolve(root, "drizzle/0009_migration.sql"));
    const bin = resolve(root, "bin");
    mkdirSync(bin);
    const marker = resolve(root, "connected");
    writeFileSync(resolve(bin, "psql"), '#!/bin/sh\ntouch "$PSQL_MARKER"\n', { mode: 0o755 });
    const result = spawnSync("bash", [resolve(scripts, "apply.sh"), "postgresql://user:password@127.0.0.1/example_test"], {
      env: { ...process.env, PATH: `${bin}:${process.env.PATH}`, CONFIRM_HOST: "127.0.0.1", PSQL_MARKER: marker },
      encoding: "utf8",
    });
    expect(result.status).not.toBe(0);
    expect(result.stderr).toContain("Missing migration file");
    expect(existsSync(marker)).toBe(false);
  });

  it("does not echo credentials when rejecting an invalid database URL", () => {
    const result = spawnSync("bash", [resolve("scripts/neon-rehearsal/apply.sh"), "https://user:secret-marker@host/db"], {
      encoding: "utf8",
    });
    expect(result.status).toBe(2);
    expect(result.stderr).toContain("invalid PostgreSQL URL");
    expect(result.stdout + result.stderr).not.toContain("secret-marker");
  });
});
