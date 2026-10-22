import worker from "../worker/src/index.js";
import { assert, equal, test } from "./helpers.js";

const dummySecret = "unit-test-placeholder-not-a-credential";
const origin = "https://alfhashut.github.io";
const githubBase = "https://api.github.com/repos/alfhashut/DokDoki-Launcher";
const siteverifyURL = "https://challenges.cloudflare.com/turnstile/v0/siteverify";
// Official Cloudflare testing values, never real production credentials.
const testToken = "XXXX.DUMMY.TOKEN.XXXX";
const testSecret = "1x0000000000000000000000000000000AA";
const actions = { bug: "report_bug", "mod-request": "request_mod", "removal-request": "request_removal" };
const valid = {
  bug: { type: "bug", dokdokiVersion: "v9.0.0", operatingSystem: "windows", whatHappened: "The launcher closes on startup.", stepsToReproduce: "Open the launcher and choose a profile.", additionalInformation: "" },
  "mod-request": { type: "mod-request", modName: "Example mod", sourceUrl: "https://example.com/mod", modAuthor: "Example author", modVersion: "1.2", additionalInformation: "" },
  "removal-request": { type: "removal-request", modName: "Example mod", sourceUrl: "https://example.com/mod", reason: "This listing should be removed.", rightsHolder: "yes", proofUrl: "https://example.com/proof", additionalInformation: "" }
};

function successful(type) {
  return [
    { status: 201, data: { number: 43, labels: [] } },
    { data: { name: type } },
    { data: { name: "status:received" } },
    { data: [{ name: type }, { name: "status:received" }] }
  ];
}

export async function exercise(input = valid.bug, options = {}) {
  const calls = [];
  const logs = [];
  const pending = [];
  const siteverifyCalls = [];
  const redirectedCalls = [];
  const timeouts = [];
  const clearedTimeouts = [];
  const queue = [...(options.responses || successful(input?.type || "bug"))];
  const saved = { fetch: globalThis.fetch, log: console.log, warn: console.warn, error: console.error, setTimeout: globalThis.setTimeout, clearTimeout: globalThis.clearTimeout };
  console.log = console.warn = console.error = (...items) => logs.push(items);
  if (options.fastTimeout || options.trackTimeouts) globalThis.setTimeout = (fn, delay) => {
    const handle = saved.setTimeout.call(globalThis, fn, options.fastTimeout ? Math.min(delay, 5) : delay);
    timeouts.push({ handle, delay });
    return handle;
  };
  if (options.trackTimeouts) globalThis.clearTimeout = (handle) => {
    clearedTimeouts.push(handle);
    return saved.clearTimeout.call(globalThis, handle);
  };
  globalThis.fetch = async (url, init) => {
    if (String(url) === siteverifyURL) {
      siteverifyCalls.push({ url: String(url), ...init, payload: JSON.parse(init.body), abortedAtStart: init.signal.aborted });
      const next = options.verification || { data: { success: true, hostname: "alfhashut.github.io", action: actions[input?.type] } };
      // Model workerd's RequestInit validation, which browser-native fetch lacks.
      if (!["follow", "manual"].includes(init.redirect)) throw new TypeError("Invalid redirect value");
      if (Object.hasOwn(next, "throwValue")) throw next.throwValue;
      if (next.offline) throw new Error(testSecret + testToken);
      if (next.waitForAbort) await new Promise((resolve, reject) => {
        init.signal.addEventListener("abort", () => reject(new DOMException("Aborted", "AbortError")), { once: true });
      });
      const body = [204, 205, 304].includes(next.status) ? null : next.raw ?? JSON.stringify(next.data);
      const response = new Response(body, { status: next.status || 200, headers: next.headers });
      if (Object.hasOwn(next, "bodyError")) response.json = async () => { throw next.bodyError; };
      return response;
    }
    assert(String(url).startsWith(githubBase + "/"), "Unexpected upstream host/repository");
    calls.push({ url: String(url), ...init, payload: init.body ? JSON.parse(init.body) : undefined });
    if (!["follow", "manual"].includes(init.redirect)) throw new TypeError("Invalid redirect value");
    const next = queue.shift();
    if (!next) throw new Error("Unexpected GitHub call");
    if (next.offline) throw new Error(dummySecret);
    if (next.waitForAbort) {
      await new Promise((resolve, reject) => {
        init.signal.addEventListener("abort", () => reject(new DOMException("Aborted", "AbortError")), { once: true });
      });
    }
    const location = new Headers(next.headers).get("Location");
    if (next.status >= 300 && next.status < 400 && location && init.redirect === "follow") {
      // Model Workers forwarding headers on automatic redirects, without making
      // any real request. Security tests must detect this credential exposure.
      redirectedCalls.push({ url: new URL(location, url).href, headers: { ...init.headers } });
      return new Response("{}");
    }
    const body = [204, 205, 304].includes(next.status) ? null : next.raw ?? JSON.stringify(next.data);
    return new Response(body, { status: next.status || 200, headers: next.headers });
  };
  try {
    const method = options.method || "POST";
    const headers = new Headers({ "Content-Type": options.contentType || "application/json" });
    if (options.origin !== null) headers.set("Origin", options.origin || origin);
    for (const [key, value] of Object.entries(options.headers || {})) headers.set(key, value);
    const submitted = input && typeof input === "object" && !Array.isArray(input) ? { ...input, turnstileToken: Object.hasOwn(input, "turnstileToken") ? input.turnstileToken : testToken } : input;
    if (options.omitToken) delete submitted.turnstileToken;
    const body = ["GET", "HEAD", "OPTIONS"].includes(method) ? undefined : options.raw ?? JSON.stringify(submitted);
    const request = new Request("https://worker.invalid" + (options.path || "/api/report"), { method, headers, body, ...(body instanceof ReadableStream ? { duplex: "half" } : {}) });
    // Browsers strip Origin/Content-Length from constructed requests. Cloudflare
    // receives these headers from the network; restore that server-side view.
    Object.defineProperty(request, "headers", { value: headers });
    const env = { GITHUB_TOKEN: dummySecret, TURNSTILE_SECRET_KEY: testSecret, SUBMISSIONS_ENABLED: "true", ...options.env };
    const response = await worker.fetch(request, env, { waitUntil: (promise) => {
      pending.push(promise);
      if (options.failScheduling) throw new Error("Simulated scheduling failure");
    } });
    await Promise.all(pending);
    const text = await response.text();
    return { status: response.status, headers: response.headers, text, data: text ? JSON.parse(text) : null, calls, siteverifyCalls, redirectedCalls, logs, timeouts, clearedTimeouts };
  } finally {
    globalThis.fetch = saved.fetch;
    globalThis.setTimeout = saved.setTimeout;
    globalThis.clearTimeout = saved.clearTimeout;
    console.log = saved.log;
    console.warn = saved.warn;
    console.error = saved.error;
  }
}

for (const type of Object.keys(valid)) {
  test(`Worker: valid ${type}, fixed issue body/title/labels and success response`, async () => {
    const result = await exercise(valid[type]);
    equal(result.status, 201);
    equal(result.data, { ok: true, requestNumber: 43, status: "received" });
    const expected = type === "bug"
      ? ["[Bug] The launcher closes on startup.", [["DokDoki version", "v9.0.0"], ["Operating system", "Windows"], ["What happened?", valid.bug.whatHappened], ["Steps to reproduce", valid.bug.stepsToReproduce], ["Additional information", "Not provided"]]]
      : type === "mod-request"
        ? ["[Mod Request] Example mod", [["Mod name", "Example mod"], ["Source", "https://example.com/mod"], ["Author", "Example author"], ["Version", "1.2"], ["Additional information", "Not provided"]]]
        : ["[Removal Request] Example mod", [["Mod name", "Example mod"], ["Source", "https://example.com/mod"], ["Reason", valid[type].reason], ["Author / rights holder", "Yes"], ["Proof / relevant URL", "https://example.com/proof"], ["Additional information", "Not provided"]]];
    equal(result.calls[0].payload, { title: expected[0], body: expected[1].map(([heading, value]) => `## ${heading}\n\n    ${value}`).join("\n\n") });
    equal(result.calls[0].url, githubBase + "/issues");
    equal(result.calls[0].method, "POST");
    equal(result.siteverifyCalls.length, 1);
    equal(result.siteverifyCalls[0].payload, { secret: testSecret, response: testToken });
    equal(result.siteverifyCalls[0].method, "POST");
    equal(result.siteverifyCalls[0].redirect, "manual");
    assert(!JSON.stringify(result.calls).includes(testToken) && !JSON.stringify(result.calls).includes(testSecret));
    equal(result.calls[3].payload, { labels: [type, "status:received"] });
    equal(result.calls[3].url, githubBase + "/issues/43/labels");
    assert(result.calls.every((call) => call.headers.Authorization === `Bearer ${dummySecret}` && call.headers["X-GitHub-Api-Version"] === "2026-03-10" && call.headers.Accept === "application/vnd.github+json" && call.headers["User-Agent"] && call.redirect === "manual"));
    equal(result.headers.get("Access-Control-Allow-Origin"), origin);
    equal(result.headers.get("Cache-Control"), "no-store");
    assert(!result.text.includes(dummySecret) && !JSON.stringify(result.logs).includes(dummySecret));
  });
}

const verificationError = { ok: false, error: "verification_failed", message: "Verification failed. Please try again." };

function assertSafeVerificationException(result, name) {
  equal(result.status, 403);
  equal(result.data, verificationError);
  equal(result.calls.length, 0, "Verification exceptions must never call GitHub");
  equal(result.logs, [[{ component: "turnstile", result: "failure", reason: "siteverify_network_error", error_name: name }]]);
  const output = result.text + JSON.stringify(result.logs);
  for (const value of [testSecret, testToken, dummySecret, valid.bug.whatHappened, "192.0.2.5"]) {
    assert(!output.includes(value), "Sensitive exception data leaked (value withheld)");
  }
}

test("Worker: Siteverify uses workerd-compatible manual redirects with its unchanged request and timeout", async () => {
  const result = await exercise(valid.bug, { trackTimeouts: true });
  equal(result.status, 201);
  equal(result.data, { ok: true, requestNumber: 43, status: "received" });
  equal(result.calls.filter((call) => call.url === githubBase + "/issues").length, 1);
  equal(result.logs, []);
  equal(result.siteverifyCalls.length, 1);
  const call = result.siteverifyCalls[0];
  equal(call.url, siteverifyURL);
  equal(call.method, "POST");
  equal(call.headers, { "Content-Type": "application/json", Accept: "application/json" });
  equal(call.payload, { secret: testSecret, response: testToken });
  equal(call.redirect, "manual");
  equal(call.abortedAtStart, false);
  equal(call.signal.aborted, false);
  equal(result.timeouts[0].delay, 8000);
  equal(result.clearedTimeouts, result.timeouts.map((timer) => timer.handle));
});

test("Worker: no error redirect mode remains in the Siteverify implementation", async () => {
  const response = await fetch("/worker/src/index.js");
  assert(response.ok);
  const source = await response.text();
  const start = source.indexOf("async function verifyTurnstile(");
  const end = source.indexOf("async function readReport(", start);
  assert(start >= 0 && end > start);
  const verification = source.slice(start, end);
  assert(/redirect:\s*"manual"/.test(verification));
  assert(!/redirect:\s*["']error["']/.test(verification));
});

for (const status of [301, 302, 303, 304, 307, 308, 400, 403, 429, 500]) {
  test(`Worker: non-ok Siteverify status ${status} is rejected without following redirects or calling GitHub`, async () => {
    const result = await exercise(valid.bug, { trackTimeouts: true, verification: {
      status,
      headers: { Location: "https://outside.invalid/never-follow" },
      data: { success: true, hostname: "alfhashut.github.io", action: "report_bug" },
      bodyError: new Error(testSecret + testToken + dummySecret)
    } });
    equal(result.status, 403);
    equal(result.data, verificationError);
    equal(result.calls.length, 0);
    equal(result.siteverifyCalls.length, 1);
    equal(result.siteverifyCalls[0].url, siteverifyURL);
    equal(result.siteverifyCalls[0].redirect, "manual");
    equal(result.siteverifyCalls[0].abortedAtStart, false);
    equal(result.logs, [[{ component: "turnstile", result: "failure", reason: "siteverify_http_error" }]]);
    equal(result.timeouts.map((timer) => timer.delay), [8000]);
    equal(result.clearedTimeouts, result.timeouts.map((timer) => timer.handle));
  });
}

test("Worker diagnostics: a real abort is distinguished by AbortError and cleans up its timer", async () => {
  const result = await exercise(valid.bug, { verification: { waitForAbort: true }, fastTimeout: true, trackTimeouts: true });
  assertSafeVerificationException(result, "AbortError");
  equal(result.siteverifyCalls[0].abortedAtStart, false);
  equal(result.siteverifyCalls[0].signal.aborted, true);
  equal(result.timeouts.map((timer) => timer.delay), [8000]);
  equal(result.clearedTimeouts, result.timeouts.map((timer) => timer.handle));
});

const sensitiveExceptionText = [testSecret, testToken, dummySecret, valid.bug.whatHappened, "192.0.2.5"].join(" ");
const throwingName = Object.defineProperty({}, "name", { get() { throw new Error(sensitiveExceptionText); } });
for (const [label, value, expected] of [
  ["error", new Error(sensitiveExceptionText), "Error"],
  ["type error", new TypeError(sensitiveExceptionText), "TypeError"],
  ["range error", new RangeError(sensitiveExceptionText), "RangeError"],
  ["abort error", new DOMException(sensitiveExceptionText, "AbortError"), "AbortError"],
  ["timeout error", new DOMException(sensitiveExceptionText, "TimeoutError"), "TimeoutError"],
  ["custom name", Object.assign(new Error(sensitiveExceptionText), { name: sensitiveExceptionText }), "UnknownError"],
  ["name getter", throwingName, "UnknownError"],
  ["string", sensitiveExceptionText, "UnknownError"],
  ["null", null, "UnknownError"]
]) {
  test(`Worker diagnostics: caught ${label} exposes only an allowlisted exception name`, async () => {
    const result = await exercise(valid.bug, { verification: { throwValue: value } });
    assertSafeVerificationException(result, expected);
    equal(result.siteverifyCalls[0].abortedAtStart, false);
  });
}

test("Worker diagnostics: response body transport failures retain the generic client response", async () => {
  const result = await exercise(valid.bug, { verification: { bodyError: new TypeError(sensitiveExceptionText) } });
  assertSafeVerificationException(result, "TypeError");
});

test("Worker diagnostics: invalid JSON is not misclassified as a fetch exception", async () => {
  const result = await exercise(valid.bug, { verification: { raw: sensitiveExceptionText } });
  equal(result.status, 403);
  equal(result.data, verificationError);
  equal(result.calls.length, 0);
  equal(result.logs, [[{ component: "turnstile", result: "failure", reason: "siteverify_invalid_json" }]]);
});

test("Worker diagnostics: each Siteverify call receives a fresh, non-aborted signal", async () => {
  const first = await exercise(valid.bug);
  const second = await exercise(valid.bug);
  equal(first.status, 201);
  equal(second.status, 201);
  const a = first.siteverifyCalls[0], b = second.siteverifyCalls[0];
  assert(a.signal !== b.signal);
  equal(a.abortedAtStart, false);
  equal(b.abortedAtStart, false);
  equal(a.signal.aborted, false);
  equal(b.signal.aborted, false);
});

for (const turnstileToken of ["", " ", null, 123, "x".repeat(2049), "invalid token"]) {
  test(`Worker: unusable Turnstile token is rejected (${typeof turnstileToken}, length ${String(turnstileToken).length})`, async () => {
    const result = await exercise({ ...valid.bug, turnstileToken });
    equal(result.status, 403);
    equal(result.data, verificationError);
    equal(result.calls.length, 0);
    equal(result.siteverifyCalls.length, 0);
  });
}

test("Worker: missing Turnstile token fails without any upstream call", async () => {
  const result = await exercise(valid.bug, { omitToken: true });
  equal(result.status, 403);
  equal(result.data, verificationError);
  equal(result.calls.length + result.siteverifyCalls.length, 0);
});

for (const verification of [
  { data: { success: false, "error-codes": ["invalid-input-response"] } },
  { data: { success: false, "error-codes": ["timeout-or-duplicate"] } },
  { data: { success: "true", hostname: "alfhashut.github.io", action: "report_bug" } },
  { data: { success: true, hostname: "alfhashut.github.io.evil.example", action: "report_bug" } },
  { data: { success: true, hostname: "localhost", action: "report_bug" } },
  { data: { success: true, hostname: "alfhashut.github.io", action: "request_mod" } },
  { data: { success: true, hostname: "alfhashut.github.io" } },
  { status: 500, data: { message: testToken + testSecret } },
  { raw: testToken + testSecret }, { offline: true }, { waitForAbort: true }
]) {
  test(`Worker: failed Siteverify never reaches GitHub (${JSON.stringify(verification)})`, async () => {
    const result = await exercise(valid.bug, { verification, fastTimeout: true });
    equal(result.status, 403);
    equal(result.data, verificationError);
    equal(result.calls.length, 0);
    equal(result.siteverifyCalls.length, 1);
    assert(!result.text.includes(testToken) && !result.text.includes(testSecret));
    assert(!JSON.stringify(result.logs).includes(testToken) && !JSON.stringify(result.logs).includes(testSecret));
    equal(Object.keys(result.siteverifyCalls[0].payload), ["secret", "response"]);
  });
}

test("Worker: missing/misconfigured Turnstile secret has the same safe failure", async () => {
  for (const TURNSTILE_SECRET_KEY of [undefined, "", "replace-me"]) {
    const result = await exercise(valid.bug, { env: { TURNSTILE_SECRET_KEY } });
    equal(result.data, verificationError);
    equal(result.status, 403);
    equal(result.calls.length + result.siteverifyCalls.length, 0);
  }
});

test("Worker: client action override is rejected, expected action comes from report type", async () => {
  const override = await exercise({ ...valid.bug, action: "request_mod" });
  equal(override.status, 400);
  equal(override.calls.length + override.siteverifyCalls.length, 0);
  for (const [type, input] of Object.entries(valid)) {
    const result = await exercise(input, { verification: { data: { success: true, hostname: "alfhashut.github.io", action: actions[type] } } });
    equal(result.status, 201);
    equal(Object.keys(result.siteverifyCalls[0].payload), ["secret", "response"]);
  }
});

const required = { bug: ["dokdokiVersion", "operatingSystem", "whatHappened", "stepsToReproduce"], "mod-request": ["modName", "sourceUrl"], "removal-request": ["modName", "sourceUrl", "reason", "rightsHolder"] };
for (const [type, keys] of Object.entries(required)) for (const key of keys) {
  test(`Worker: ${type} requires ${key}`, async () => {
    const input = { ...valid[type] };
    delete input[key];
    const result = await exercise(input);
    equal(result.status, 400);
    equal(result.data.error, "validation_error");
    equal(result.data.field, key);
    equal(result.calls.length, 0);
  });
}

for (const input of [null, [], {}, { ...valid.bug, type: "unknown" }, { ...valid.bug, type: "__proto__" }, { ...valid.bug, dokdokiVersion: 123 }, { ...valid.bug, whatHappened: "   " }, { ...valid.bug, operatingSystem: "macos" }, { ...valid["removal-request"], rightsHolder: "maybe" }, { ...valid.bug, whatHappened: "too short" }, { ...valid.bug, dokdokiVersion: "v1\nmetadata" }, { ...valid.bug, additionalInformation: "bad\u0000control" }, { ...valid.bug, labels: ["admin"] }, { ...valid.bug, repository: "another/repo" }, { ...valid.bug, state: "closed" }]) {
  test(`Worker: rejects malformed schema ${JSON.stringify(input).slice(0, 85)}`, async () => {
    const result = await exercise(input);
    equal(result.status, 400);
    equal(result.calls.length, 0);
  });
}

for (const sourceUrl of ["javascript:alert(1)", "ftp://example.com/mod", "https://user:pass@example.com/mod", "not a URL", "https://example.com/\nlabels"]) {
  test(`Worker: rejects invalid URL ${JSON.stringify(sourceUrl)}`, async () => {
    const result = await exercise({ ...valid["mod-request"], sourceUrl });
    equal(result.status, 400);
    equal(result.data.field, "sourceUrl");
    equal(result.calls.length, 0);
  });
}

test("Worker: rejects oversized fields, declared length, and streamed bodies", async () => {
  const field = await exercise({ ...valid.bug, additionalInformation: "x".repeat(5001) });
  equal(field.status, 400);
  const declared = await exercise(valid.bug, { headers: { "Content-Length": "65537" } });
  equal(declared.status, 413);
  const stream = new ReadableStream({ start(controller) { controller.enqueue(new Uint8Array(65537)); controller.close(); } });
  const streamed = await exercise(valid.bug, { raw: stream });
  equal(streamed.status, 413);
  assert([field, declared, streamed].every((result) => result.calls.length === 0));
});

test("Worker: rejects malformed JSON, invalid UTF-8 and wrong content type", async () => {
  equal((await exercise(valid.bug, { raw: "{not json" })).status, 400);
  equal((await exercise(valid.bug, { raw: new Uint8Array([255, 254]) })).status, 400);
  equal((await exercise(valid.bug, { contentType: "text/plain" })).status, 415);
});

test("Worker: normalizes text and makes submitted Markdown/metadata literal", async () => {
  const result = await exercise({ ...valid.bug, dokdokiVersion: " v9.0.0 ", whatHappened: "<failure>\r\nsecond line", additionalInformation: "## Injected\r\n<!-- labels: admin -->\r\n```\r\n@someone" }, { headers: { "CF-Connecting-IP": "192.0.2.5", "CF-Ray": "test-ray" } });
  const payload = result.calls[0].payload;
  equal(payload.title, "[Bug] failure second line");
  assert(payload.body.includes("    ## Injected\n    <!-- labels: admin -->\n    ```\n    @someone"));
  assert(!payload.body.includes("\n## Injected") && !payload.body.includes("192.0.2.5") && !payload.body.includes("test-ray"));
});

test("Worker: POST-only route, minimal health, and unknown routes", async () => {
  const method = await exercise(valid.bug, { method: "GET" });
  equal(method.status, 405);
  equal(method.headers.get("Allow"), "POST, OPTIONS");
  equal((await exercise(valid.bug, { path: "/health", method: "GET", origin: null })).data, { ok: true });
  equal((await exercise(valid.bug, { path: "/other" })).status, 404);
});

test("Worker: strict CORS preflight and explicitly configured loopback origin", async () => {
  const options = { method: "OPTIONS", headers: { "Access-Control-Request-Method": "POST", "Access-Control-Request-Headers": "content-type" } };
  const allowed = await exercise(valid.bug, options);
  equal(allowed.status, 204);
  equal(allowed.headers.get("Access-Control-Allow-Origin"), origin);
  equal(allowed.headers.get("Access-Control-Allow-Methods"), "POST");
  assert(!allowed.headers.has("Access-Control-Allow-Credentials"));
  equal((await exercise(valid.bug, { ...options, headers: { ...options.headers, "Access-Control-Request-Headers": "authorization" } })).status, 403);
  equal((await exercise(valid.bug, { ...options, headers: { "Access-Control-Request-Method": "DELETE" } })).status, 403);
  const local = await exercise(valid.bug, { origin: "http://127.0.0.1:8000", env: { ALLOWED_ORIGINS: "https://alfhashut.github.io,http://127.0.0.1:8000" } });
  equal(local.status, 201);
  equal(local.headers.get("Access-Control-Allow-Origin"), "http://127.0.0.1:8000");
});

for (const rejected of [null, "null", "https://evil.example", "https://alfhashut.github.io.evil.example", "https://alfhashut.github.io/DokDoki-Launcher/", "http://127.0.0.1:8000"]) {
  test(`Worker: rejects origin ${rejected} without upstream calls or permissive CORS`, async () => {
    const result = await exercise(valid.bug, { origin: rejected });
    equal(result.status, 403);
    assert(!result.headers.has("Access-Control-Allow-Origin"));
    equal(result.calls.length, 0);
    equal(result.siteverifyCalls.length, 0);
  });
}

test("Worker: wildcard and malformed configured origins fail closed", async () => {
  for (const configured of ["*", origin + "/path", "http://public.example", "https://user:pass@alfhashut.github.io"]) {
    equal((await exercise(valid.bug, { env: { ALLOWED_ORIGINS: configured } })).status, 403);
  }
});

test("Worker: default/false/mistyped submission gates and placeholder credentials fail closed", async () => {
  for (const SUBMISSIONS_ENABLED of [undefined, "false", true, "TRUE"]) {
    const result = await exercise(valid.bug, { env: { SUBMISSIONS_ENABLED } });
    equal(result.status, 503);
    equal(result.data.error, "submissions_disabled");
    equal(result.calls.length, 0);
    equal(result.siteverifyCalls.length, 0);
  }
  for (const GITHUB_TOKEN of [undefined, "", "replace-me"]) {
    const result = await exercise(valid.bug, { env: { GITHUB_TOKEN } });
    equal(result.status, 503);
    equal(result.data.error, "submission_failed");
    equal(result.calls.length, 0);
  }
});

for (const failure of [{ status: 401, data: { message: dummySecret } }, { status: 403, data: { message: dummySecret } }, { status: 422, data: { errors: [dummySecret] } }, { status: 500, raw: dummySecret }, { status: 201, data: { number: "43" } }, { status: 201, raw: "invalid JSON" }, { offline: true }, { waitForAbort: true }]) {
  test(`Worker: upstream failure is safe, never retried (${JSON.stringify(failure)})`, async () => {
    const result = await exercise(valid.bug, { responses: [failure], fastTimeout: true });
    equal(result.status, 502);
    equal(result.data, { ok: false, error: "submission_failed", message: "The request could not be submitted right now." });
    equal(result.calls.length, 1);
    assert(!result.text.includes(dummySecret) && !JSON.stringify(result.logs).includes(dummySecret));
  });
}

test("Worker: all runtime outbound fetches explicitly use manual redirect rejection", async () => {
  const response = await fetch("/worker/src/index.js");
  assert(response.ok);
  const source = await response.text();
  equal([...source.matchAll(/\bredirect:\s*["']([^"']+)["']/g)].map((match) => match[1]), ["manual", "manual"]);
  assert(!/\bredirect:\s*["'](?:error|follow)["']/.test(source));
});

function assertNoGitHubRedirectFollow(result) {
  equal(result.redirectedCalls, [], "Authorization must never reach a redirect destination");
  for (const call of result.calls) {
    assert(call.url.startsWith(githubBase + "/"));
    equal(call.redirect, "manual");
    equal(call.headers.Authorization, `Bearer ${dummySecret}`);
  }
  equal(result.siteverifyCalls.length, 1);
  equal(result.siteverifyCalls[0].redirect, "manual");
  assert(!result.text.includes(dummySecret) && !JSON.stringify(result.logs).includes(dummySecret));
  equal(result.timeouts[0].delay, 8000);
  assert(result.timeouts.slice(1).every((timer) => timer.delay === 10000));
  equal(result.clearedTimeouts, result.timeouts.map((timer) => timer.handle));
}

for (const status of [301, 302, 303, 304, 307, 308]) {
  const headers = { Location: "https://redirect-target.invalid/credential-trap" };
  test(`Worker: GitHub issue redirect ${status} fails safely without forwarding Authorization`, async () => {
    const result = await exercise(valid.bug, { trackTimeouts: true, responses: [
      { status, headers, data: { number: 43, message: dummySecret } }
    ] });
    equal(result.status, 502);
    equal(result.data, { ok: false, error: "submission_failed", message: "The request could not be submitted right now." });
    equal(result.calls.length, 1, "A redirect must not retry creation or start label requests");
    equal(result.calls[0].url, githubBase + "/issues");
    equal(result.calls[0].method, "POST");
    equal(result.logs, [["report_creation_failed"]]);
    assertNoGitHubRedirectFollow(result);
  });

  test(`Worker: GitHub label lookup redirect ${status} is rejected without losing the issue`, async () => {
    const responses = successful("bug");
    responses[1] = { status, headers, data: { name: "bug", message: dummySecret } };
    const result = await exercise(valid.bug, { responses, trackTimeouts: true });
    equal(result.status, 201);
    equal(result.data, { ok: true, requestNumber: 43, status: "received" });
    equal(result.calls.filter((call) => call.url === githubBase + "/issues").length, 1);
    equal(result.calls[1].url, githubBase + "/labels/bug");
    equal(result.calls[1].method, "GET");
    equal(result.calls.at(-1).payload, { labels: ["status:received"] });
    equal(result.logs, [["report_label_unavailable"]]);
    assertNoGitHubRedirectFollow(result);
  });

  test(`Worker: GitHub label application redirect ${status} is rejected without losing the issue`, async () => {
    const responses = successful("bug");
    responses[3] = { status, headers, data: [{ name: "bug" }, { name: "status:received" }] };
    const result = await exercise(valid.bug, { responses, trackTimeouts: true });
    equal(result.status, 201);
    equal(result.data, { ok: true, requestNumber: 43, status: "received" });
    equal(result.calls.length, 4);
    equal(result.calls.filter((call) => call.url === githubBase + "/issues").length, 1);
    equal(result.calls[3].url, githubBase + "/issues/43/labels");
    equal(result.calls[3].method, "POST");
    equal(result.logs, [["report_labels_incomplete"]]);
    assertNoGitHubRedirectFollow(result);
  });
}

test("Worker: absent labels do not fail or recreate a confirmed issue", async () => {
  const result = await exercise(valid.bug, { responses: [{ status: 201, data: { number: 43 } }, { status: 404, data: {} }, { status: 404, data: {} }] });
  equal(result.status, 201);
  equal(result.calls.filter((call) => call.method === "POST").length, 1);
  equal(result.logs, [["report_label_unavailable"], ["report_label_unavailable"]]);
});

test("Worker: applies only existing labels and never creates repository labels", async () => {
  const result = await exercise(valid.bug, { responses: [{ status: 201, data: { number: 43 } }, { data: { name: "bug" } }, { status: 404, data: {} }, { data: [{ name: "bug" }] }] });
  equal(result.status, 201);
  equal(result.calls.at(-1).payload, { labels: ["bug"] });
  assert(!result.calls.some((call) => call.url === githubBase + "/labels" && call.method === "POST"));
});

test("Worker: scheduling failure cannot report a confirmed issue as failed", async () => {
  const result = await exercise(valid.bug, { failScheduling: true });
  equal(result.status, 201);
  equal(result.calls.filter((call) => call.url === githubBase + "/issues").length, 1);
  assert(result.logs.some((items) => items[0] === "report_labels_incomplete"));
});

test("Worker: optional fields may be absent and title summaries stay bounded", async () => {
  const result = await exercise({ type: "mod-request", modName: "a".repeat(200), sourceUrl: "https://example.com/mod" });
  equal(result.status, 201);
  equal(result.calls[0].payload.title, "[Mod Request] " + "a".repeat(100));
  equal(result.calls[0].payload.body.match(/Not provided/g).length, 3);
});

for (const failure of [{ status: 422, data: { message: dummySecret } }, { status: 403, data: { message: dummySecret } }, { offline: true }, { waitForAbort: true }, { data: [] }]) {
  test(`Worker: label application failure remains a safe success (${JSON.stringify(failure)})`, async () => {
    const responses = successful("bug");
    responses[3] = failure;
    const result = await exercise(valid.bug, { responses, fastTimeout: true });
    equal(result.status, 201);
    equal(result.data.requestNumber, 43);
    equal(result.calls.filter((call) => call.url === githubBase + "/issues").length, 1);
    assert(result.logs.length > 0 && !JSON.stringify(result.logs).includes(dummySecret) && !result.text.includes(dummySecret));
  });
}
