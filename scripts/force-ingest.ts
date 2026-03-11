import { processSingleJob } from "../lib/ingestion/worker";
import * as dotenv from "dotenv";

dotenv.config({ path: ".env.local" });

async function run() {
    console.log("Starting forced local test ingestion on job ID 1...");
    const start = Date.now();
    const res = await processSingleJob(1);
    console.log("Done in", (Date.now() - start) / 1000, "seconds");
    console.log(JSON.stringify(res, null, 2));
}

run().catch(console.error).finally(() => process.exit(0));
