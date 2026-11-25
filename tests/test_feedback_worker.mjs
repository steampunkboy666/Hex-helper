import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { readFile } from "node:fs/promises";
import worker from "../feedback-worker/worker.mjs";

const bytes = await readFile(process.argv[2]);
const endpoint = "https://hex-helper-feedback.test.workers.dev/v1/feedback";
const receipt = createHash("sha256").update(bytes).digest("hex").slice(0, 32);
const store = new Map();
let accept = true;
let writes = 0;
let failStorage = false;
const env = {
  UPLOAD_LIMIT: {
    limit({ key }) {
      assert.equal(key, "203.0.113.5");
      return { success: accept };
    },
  },
  FEEDBACK: {
    async get(key, { type }) {
      assert.equal(type, "stream");
      return store.has(key) ? new Response(store.get(key)).body : null;
    },
    async put(key, content, options) {
      if (failStorage) throw new Error("KV temporarily unavailable");
      assert.equal(options.expirationTtl, 30 * 86400);
      assert.equal(options.metadata.size, bytes.length);
      assert.deepEqual(Buffer.from(content), bytes);
      writes += 1;
      store.set(key, content);
    },
  },
};

function submit(body = bytes, headers = {}) {
  return worker.fetch(new Request(endpoint, {
    method: "POST", body, headers: {
      "CF-Connecting-IP": "203.0.113.5", "Content-Type": "application/zip",
      "Content-Length": String(body.length), ...headers,
    },
  }), env);
}

async function expectError(reply, status, error) {
  assert.equal(reply.status, status);
  assert.deepEqual(await reply.json(), { error });
}

const health = await worker.fetch(new Request(new URL("/health", endpoint)), env);
assert.equal(health.status, 200);
assert.equal((await health.json()).service, "hex-helper-feedback");

await expectError(await submit(bytes, { "Content-Type": "text/plain" }), 415, "zip_required");
await expectError(await submit(bytes, { "Content-Length": "" }), 411, "length_required");
await expectError(await submit(bytes, { "Content-Length": "1048577" }), 413, "too_large");
await expectError(await submit(bytes, { "Content-Length": String(bytes.length - 1) }), 400, "invalid_package");
await expectError(await submit(Buffer.from("broken ZIP")), 400, "invalid_package");
const wrongName = Buffer.from(bytes);
wrongName[wrongName.indexOf("screenshot.jpg")] = "x".charCodeAt(0);
await expectError(await submit(wrongName), 400, "invalid_package");

failStorage = true;
await expectError(await submit(), 503, "storage_unavailable");
failStorage = false;
const created = await submit();
assert.equal(created.status, 201);
assert.deepEqual(await created.json(), { id: receipt });
const duplicate = await submit();
assert.equal(duplicate.status, 200);
assert.deepEqual(await duplicate.json(), { id: receipt });
assert.equal(writes, 1);

await expectError(await worker.fetch(new Request(endpoint), env), 405, "method_not_allowed");
await expectError(await worker.fetch(new Request(new URL(`/feedback/${receipt}`, endpoint)), env), 404, "not_found");
accept = false;
await expectError(await submit(), 429, "rate_limited");
console.log("Worker: health, ZIP validation, private storage, retry, and rate limit passed.");
