import { afterEach, describe, expect, it } from "vitest";
import { createServer, type Server } from "node:http";
import { once } from "node:events";
import { execFile } from "node:child_process";
import { promisify } from "node:util";
import path from "node:path";

const execFileAsync = promisify(execFile);
const root = process.cwd();
const env = { ...process.env, PYTHONPATH: path.join(root, "intelligence", "src") };
const servers: Server[] = [];

async function serve(cost: unknown, status = 200) {
  let auth = "";
  let body: unknown;
  const server = createServer(async (req, res) => {
    auth = req.headers.authorization ?? "";
    let raw = "";
    for await (const chunk of req) raw += chunk;
    body = JSON.parse(raw);
    res.writeHead(status, { "content-type": "application/json" });
    res.end(JSON.stringify({ cost }));
  });
  servers.push(server);
  server.listen(0, "127.0.0.1");
  await once(server, "listening");
  const address = server.address();
  if (!address || typeof address === "string") throw new Error("server did not bind");
  return { url: `http://127.0.0.1:${address.port}`, auth: () => auth, body: () => body };
}

async function probe(url: string, options: string[] = []) {
  return execFileAsync("python3", ["-m", "scribe_perf.cli", "probe", "--base-url", url,
    "--count", "2", "--question", "What changed?", "--question", "What is the rule?", ...options], { cwd: root, env });
}

afterEach(() => {
  for (const server of servers.splice(0)) server.close();
});

describe("scribe-perf HTTP probe", () => {
  it("calls retrieval with the opaque bearer token and reports estimated cost without exposing the token", async () => {
    const stub = await serve({ usd: 0.000004, estimated: true });
    const secret = "opaque-test-token-must-not-appear";
    const result = await probe(stub.url, ["--token", secret]);
    expect(stub.auth()).toBe(`Bearer ${secret}`);
    expect(stub.body()).toEqual({ query: "What is the rule?" });
    expect(result.stdout).toContain("estimated $/query: 0.00000400");
    expect(result.stdout).toMatch(/latency p50: [\d.]+ ms/);
    expect(result.stdout).toMatch(/latency p95: [\d.]+ ms/);
    expect(result.stdout + result.stderr).not.toContain(secret);
  }, 20000);

  it("reports missing retrieval cost as NOT MEASURED and rejects zero requests", async () => {
    const stub = await serve(null);
    const result = await probe(stub.url, ["--token", "opaque", "--count", "1"]);
    expect(result.stdout).toContain("$/query: NOT MEASURED");
    await expect(execFileAsync("python3", ["-m", "scribe_perf.cli", "probe", "--base-url", stub.url,
      "--count", "0", "--question", "q", "--token", "opaque"], { cwd: root, env }))
      .rejects.toMatchObject({ code: 2 });
  }, 20000);

  it("uses nearest-rank percentiles on a literal sample", async () => {
    const result = await execFileAsync("python3", ["-c",
      "from scribe_perf.stats import percentile; print(percentile([9, 1, 5, 3, 7], 50), percentile([9, 1, 5, 3, 7], 95))"],
      { cwd: root, env });
    expect(result.stdout.trim()).toBe("5 9");
  }, 20000);

  it("sanitizes HTTP failures so the token never appears in diagnostics", async () => {
    const stub = await serve(null, 401);
    const secret = "must-not-leak-from-http-error";
    let captured: { code?: number; stdout?: string; stderr?: string } = {};
    try {
      await probe(stub.url, ["--token", secret]);
    } catch (error) {
      captured = error as typeof captured;
    }
    expect(captured.code).toBe(1);
    expect(captured.stderr).toContain("HTTPError");
    expect(`${captured.stdout ?? ""}${captured.stderr ?? ""}`).not.toContain(secret);
  }, 20000);
});
