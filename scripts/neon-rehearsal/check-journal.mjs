import { createHash } from "node:crypto";
import { readFileSync, readdirSync, realpathSync } from "node:fs";
import { resolve } from "node:path";
import { pathToFileURL } from "node:url";

/** Validate the complete local history before a rehearsal can connect or write. */
export function migrationManifest(root) {
  const directory = resolve(root, "drizzle");
  const journal = JSON.parse(readFileSync(resolve(directory, "meta/_journal.json"), "utf8"));
  if (journal.dialect !== "postgresql" || !Array.isArray(journal.entries) || !journal.entries.length) {
    throw new Error("Expected a nonempty PostgreSQL migration journal");
  }
  const files = new Set(readdirSync(directory).filter((name) => /^\d{4}_.*\.sql$/.test(name)));
  let previousTime = -1;
  const manifest = journal.entries.map((entry, index) => {
    if (entry.idx !== index || typeof entry.tag !== "string" ||
        !new RegExp(`^${String(index).padStart(4, "0")}_[a-z0-9_]+$`).test(entry.tag)) {
      throw new Error(`Invalid or out-of-order migration at journal index ${index}`);
    }
    if (!Number.isSafeInteger(entry.when) || entry.when <= previousTime) {
      throw new Error(`Migration timestamps must increase: ${entry.tag}`);
    }
    previousTime = entry.when;
    const filename = `${entry.tag}.sql`;
    if (!files.delete(filename)) throw new Error(`Missing migration file: ${filename}`);
    const hash = createHash("sha256").update(readFileSync(resolve(directory, filename))).digest("hex");
    return { tag: entry.tag, when: entry.when, hash };
  });
  if (files.size) throw new Error(`Unjournaled migration files: ${[...files].sort().join(", ")}`);
  return manifest.filter((entry) => Number(entry.tag.slice(0, 4)) >= 8);
}

if (process.argv[1] && import.meta.url === pathToFileURL(realpathSync(process.argv[1])).href) {
  try {
    const manifest = migrationManifest(process.argv[2] ?? process.cwd());
    for (const entry of manifest) console.log(`${entry.tag}\t${entry.when}\t${entry.hash}`);
  } catch (error) {
    console.error(`Migration journal check failed: ${error.message}`);
    process.exitCode = 1;
  }
}
