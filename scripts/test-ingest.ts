import { db } from "../lib/db-config";
import { processQueuedIngestionJobs } from "../lib/ingestion/worker";
import * as dotenv from "dotenv";

dotenv.config({ path: ".env.local" });

async function run() {
    console.log("Starting local test ingestion...");
    const start = Date.now();
    const res = await processQueuedIngestionJobs(1);
    console.log("Done in", (Date.now() - start) / 1000, "seconds");
    console.log(JSON.stringify(res, null, 2));
}

run().catch(console.error).finally(() => process.exit(0));
