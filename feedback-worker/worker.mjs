const MAX_PACKAGE = 1_048_576;
const RETENTION_SECONDS = 30 * 24 * 60 * 60;
const FILE_LIMITS = new Map([
  ["screenshot.jpg", MAX_PACKAGE],
  ["description.txt", 32_768],
  ["diagnostics.txt", 65_536],
]);

function respond(status, data) {
  return Response.json(data, { status, headers: { "Cache-Control": "no-store" } });
}

function validPackage(bytes) {
  // Read the ZIP index without expanding visitor-controlled data.
  const view = new DataView(bytes.buffer, bytes.byteOffset, bytes.byteLength);
  const end = bytes.length - 22;
  if (end < 0 || view.getUint32(end, true) !== 0x06054b50 ||
      view.getUint32(end + 4, true) !== 0 || view.getUint16(end + 20, true) !== 0 ||
      view.getUint16(end + 8, true) !== 3 || view.getUint16(end + 10, true) !== 3) {
    return false;
  }
  const index = view.getUint32(end + 16, true);
  if (index + view.getUint32(end + 12, true) !== end) return false;
  const names = new Set();
  const decoder = new TextDecoder();
  let start = index;
  let previousEnd = 0;
  while (start + 46 <= end && names.size < FILE_LIMITS.size) {
    if (view.getUint32(start, true) !== 0x02014b50) return false;
    const nameSize = view.getUint16(start + 28, true);
    const next = start + 46 + nameSize;
    if (next > end || view.getUint16(start + 30, true) !== 0 ||
        view.getUint16(start + 32, true) !== 0) return false;
    const name = decoder.decode(bytes.subarray(start + 46, next));
    const limit = FILE_LIMITS.get(name);
    const size = view.getUint32(start + 24, true);
    const offset = view.getUint32(start + 42, true);
    const compressed = view.getUint32(start + 20, true);
    if (!limit || names.has(name) || size === 0 || size > limit ||
        offset !== previousEnd || offset + 30 + nameSize + compressed > index ||
        view.getUint32(offset, true) !== 0x04034b50 ||
        view.getUint16(start + 8, true) !== 0 || view.getUint16(offset + 6, true) !== 0 ||
        view.getUint16(start + 10, true) !== 8 || view.getUint16(offset + 8, true) !== 8 ||
        view.getUint32(start + 16, true) !== view.getUint32(offset + 14, true) ||
        view.getUint32(offset + 18, true) !== compressed || view.getUint32(offset + 22, true) !== size ||
        view.getUint16(offset + 26, true) !== nameSize || view.getUint16(offset + 28, true) !== 0 ||
        decoder.decode(bytes.subarray(offset + 30, offset + 30 + nameSize)) !== name) return false;
    names.add(name);
    previousEnd = offset + 30 + nameSize + compressed;
    start = next;
  }
  return start === end && previousEnd === index && names.size === FILE_LIMITS.size;
}

async function readBody(request, size) {
  const bytes = new Uint8Array(size);
  if (!request.body) return null;
  const reader = request.body.getReader();
  let offset = 0;
  try {
    while (true) {
      const { value, done } = await reader.read();
      if (done) return offset === size ? bytes : null;
      if (offset + value.length > size) {
        await reader.cancel();
        return null;
      }
      bytes.set(value, offset);
      offset += value.length;
    }
  } finally {
    reader.releaseLock();
  }
}

export default {
  async fetch(request, env) {
    const path = new URL(request.url).pathname;
    if (path === "/health" && request.method === "GET") {
      return respond(200, { service: "hex-helper-feedback" });
    }
    if (path !== "/v1/feedback") return respond(404, { error: "not_found" });
    if (request.method !== "POST") return respond(405, { error: "method_not_allowed" });
    if (request.headers.get("Content-Type") !== "application/zip") {
      return respond(415, { error: "zip_required" });
    }
    const length = request.headers.get("Content-Length");
    if (!length || !/^\d+$/.test(length)) return respond(411, { error: "length_required" });
    const size = Number(length);
    if (size > MAX_PACKAGE) return respond(413, { error: "too_large" });
    try {
      const address = request.headers.get("CF-Connecting-IP") || "unknown";
      if (!(await env.UPLOAD_LIMIT.limit({ key: address })).success) {
        return respond(429, { error: "rate_limited" });
      }
      const bytes = await readBody(request, size);
      if (!bytes || !validPackage(bytes)) return respond(400, { error: "invalid_package" });
      const digest = await crypto.subtle.digest("SHA-256", bytes);
      const id = Array.from(new Uint8Array(digest), (byte) => byte.toString(16).padStart(2, "0")).join("").slice(0, 32);
      const key = "feedback/" + id;
      const previous = await env.FEEDBACK.get(key, { type: "stream" });
      if (previous) {
        await previous.cancel();
        return respond(200, { id });
      }
      await env.FEEDBACK.put(key, bytes.buffer, {
        expirationTtl: RETENTION_SECONDS,
        metadata: { receivedAt: new Date().toISOString(), size },
      });
      return respond(201, { id });
    } catch {
      return respond(503, { error: "storage_unavailable" });
    }
  },
};
