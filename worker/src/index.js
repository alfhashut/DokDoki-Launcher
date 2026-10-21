const REPOSITORY = "alfhashut/DokDoki-Launcher";
const GITHUB_API = `https://api.github.com/repos/${REPOSITORY}`;
// Verified against GitHub's official Issues/Labels REST documentation, 2026-10-22.
const GITHUB_API_VERSION = "2026-03-10";
const MAX_BODY_BYTES = 64 * 1024;
const FAILURE_MESSAGE = "The request could not be submitted right now.";
const SITEVERIFY_URL = "https://challenges.cloudflare.com/turnstile/v0/siteverify";
const TURNSTILE_HOSTNAME = "alfhashut.github.io";
const TURNSTILE_ACTIONS = {
  bug: "report_bug",
  "mod-request": "request_mod",
  "removal-request": "request_removal"
};

// Only these fields become issue content. Callers cannot supply GitHub metadata.
const SCHEMAS = {
  bug: {
    prefix: "[Bug] ", summary: "whatHappened",
    fields: {
      dokdokiVersion: { heading: "DokDoki version", max: 80, required: true, singleLine: true },
      operatingSystem: { heading: "Operating system", max: 7, required: true, choices: { windows: "Windows", linux: "Linux" } },
      whatHappened: { heading: "What happened?", max: 5000, min: 10, required: true },
      stepsToReproduce: { heading: "Steps to reproduce", max: 5000, min: 10, required: true },
      additionalInformation: { heading: "Additional information", max: 5000 }
    }
  },
  "mod-request": {
    prefix: "[Mod Request] ", summary: "modName",
    fields: {
      modName: { heading: "Mod name", max: 200, required: true, singleLine: true },
      sourceUrl: { heading: "Source", max: 2048, required: true, url: true },
      modAuthor: { heading: "Author", max: 200, singleLine: true },
      modVersion: { heading: "Version", max: 80, singleLine: true },
      additionalInformation: { heading: "Additional information", max: 5000 }
    }
  },
  "removal-request": {
    prefix: "[Removal Request] ", summary: "modName",
    fields: {
      modName: { heading: "Mod name", max: 200, required: true, singleLine: true },
      sourceUrl: { heading: "Source", max: 2048, required: true, url: true },
      reason: { heading: "Reason", max: 5000, min: 10, required: true },
      rightsHolder: { heading: "Author / rights holder", max: 3, required: true, choices: { yes: "Yes", no: "No" } },
      proofUrl: { heading: "Proof / relevant URL", max: 2048, url: true },
      additionalInformation: { heading: "Additional information", max: 5000 }
    }
  }
};

class ValidationError extends Error {
  constructor(message, field, status = 400) {
    super(message);
    this.field = field;
    this.status = status;
  }
}

function normalizeReport(input) {
  if (!input || typeof input !== "object" || Array.isArray(input)) throw new ValidationError("Send a JSON object.");
  if (typeof input.type !== "string" || !Object.hasOwn(SCHEMAS, input.type)) throw new ValidationError("Choose a supported report type.", "type");
  const schema = SCHEMAS[input.type];
  if (Object.keys(input).some((key) => key !== "type" && key !== "turnstileToken" && !Object.hasOwn(schema.fields, key))) {
    throw new ValidationError("The report contains unsupported fields.");
  }
  const values = {};
  for (const [key, rule] of Object.entries(schema.fields)) {
    const raw = input[key] === undefined ? "" : input[key];
    if (typeof raw !== "string") throw new ValidationError(`${rule.heading} must be text.`, key);
    if (raw.length > rule.max) throw new ValidationError(`${rule.heading} must be no longer than ${rule.max} characters.`, key);
    const value = raw.replace(/\r\n?/g, "\n").normalize("NFC").trim();
    if (/[\u0000-\u0008\u000b\u000c\u000e-\u001f\u007f]/u.test(value) || (rule.singleLine && /[\n\t\u2028\u2029]/u.test(value))) {
      throw new ValidationError(`${rule.heading} contains unsupported control characters.`, key);
    }
    if (rule.required && !value) throw new ValidationError(`${rule.heading} is required.`, key);
    if (value && rule.min && value.length < rule.min) throw new ValidationError(`${rule.heading} must contain at least ${rule.min} characters.`, key);
    if (value.length > rule.max) throw new ValidationError(`${rule.heading} is too long.`, key);
    if (rule.choices && !Object.hasOwn(rule.choices, value)) throw new ValidationError(`Choose a supported value for ${rule.heading}.`, key);
    if (value && rule.url) {
      let url;
      try { url = new URL(value); } catch { /* Rejected below. */ }
      if (!url || !["http:", "https:"].includes(url.protocol) || !url.hostname || url.username || url.password || /\s/u.test(value)) {
        throw new ValidationError(`${rule.heading} must be a full http:// or https:// URL without login details.`, key);
      }
    }
    values[key] = rule.choices ? rule.choices[value] : value;
  }
  const summary = Array.from(values[schema.summary].replace(/\s+/gu, " ").replace(/[<>\u202a-\u202e\u2066-\u2069]/gu, "")).slice(0, 100).join("");
  // Indented code blocks keep submitted Markdown, HTML comments, and metadata
  // literal. A submitted heading cannot become a new machine-readable section.
  const body = Object.entries(schema.fields).map(([key, rule]) => {
    const literal = (values[key] || "Not provided").split("\n").map((line) => `    ${line}`).join("\n");
    return `## ${rule.heading}\n\n${literal}`;
  }).join("\n\n");
  return { title: schema.prefix + (summary || "Report"), body, labels: [input.type, "status:received"] };
}

async function verifyTurnstile(token, type, secret) {
  // Token failures and configuration failures have the same public response.
  // Neither these values nor the raw Siteverify response enter logs or Issues.
  if (typeof token !== "string" || !token.trim() || token.length > 2048 || /\s/u.test(token) ||
      typeof secret !== "string" || !secret.trim() || secret === "replace-me") return false;
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), 8000);
  try {
    const response = await fetch(SITEVERIFY_URL, {
      method: "POST",
      headers: { "Content-Type": "application/json", Accept: "application/json" },
      // No remoteip, CF headers, user agent, or other requester metadata.
      body: JSON.stringify({ secret, response: token }),
      redirect: "error",
      signal: controller.signal
    });
    if (!response.ok) return false;
    const result = await response.json();
    return result?.success === true && result.hostname === TURNSTILE_HOSTNAME && result.action === TURNSTILE_ACTIONS[type];
  } catch {
    return false;
  } finally {
    clearTimeout(timeout);
  }
}

async function readReport(request) {
  if (!/^application\/json(?:\s*;|$)/i.test(request.headers.get("content-type") || "")) {
    throw new ValidationError("Use application/json for submissions.", undefined, 415);
  }
  const declaredSize = request.headers.get("content-length");
  if (declaredSize && (!/^\d+$/.test(declaredSize) || Number(declaredSize) > MAX_BODY_BYTES)) {
    throw new ValidationError("Keep submissions below 64 KiB.", undefined, 413);
  }
  if (!request.body) throw new ValidationError("Send a JSON object.");
  const reader = request.body.getReader();
  const decoder = new TextDecoder("utf-8", { fatal: true });
  let size = 0;
  let text = "";
  try {
    while (true) {
      const { value, done } = await reader.read();
      if (done) break;
      size += value.byteLength;
      if (size > MAX_BODY_BYTES) {
        await reader.cancel();
        throw new ValidationError("Keep submissions below 64 KiB.", undefined, 413);
      }
      text += decoder.decode(value, { stream: true });
    }
    text += decoder.decode();
    return JSON.parse(text);
  } catch (error) {
    if (error instanceof ValidationError) throw error;
    throw new ValidationError("Send valid UTF-8 JSON.");
  } finally {
    reader.releaseLock();
  }
}

function allowedOrigin(request, env) {
  const origin = request.headers.get("origin");
  if (!origin || origin === "null") return null;
  const configured = typeof env.ALLOWED_ORIGINS === "string" ? env.ALLOWED_ORIGINS : "https://alfhashut.github.io";
  const allowed = configured.split(",").map((value) => value.trim()).filter((value) => {
    try {
      const url = new URL(value);
      return url.origin === value && !url.username && !url.password &&
        (url.protocol === "https:" || (url.protocol === "http:" && ["localhost", "127.0.0.1", "[::1]"].includes(url.hostname)));
    } catch { return false; }
  });
  return allowed.includes(origin) ? origin : null;
}

function json(data, status, origin, extraHeaders = {}) {
  const headers = new Headers({
    "Content-Type": "application/json; charset=utf-8",
    "Cache-Control": "no-store",
    "X-Content-Type-Options": "nosniff",
    Vary: "Origin",
    ...extraHeaders
  });
  if (origin) headers.set("Access-Control-Allow-Origin", origin);
  return new Response(JSON.stringify(data), { status, headers });
}

async function github(path, token, method = "GET", payload) {
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), 10000);
  try {
    const response = await fetch(GITHUB_API + path, {
      method,
      headers: {
        Accept: "application/vnd.github+json",
        Authorization: `Bearer ${token}`,
        "X-GitHub-Api-Version": GITHUB_API_VERSION,
        "User-Agent": "DokDoki-Launcher-Requests",
        ...(payload ? { "Content-Type": "application/json" } : {})
      },
      ...(payload ? { body: JSON.stringify(payload) } : {}),
      redirect: "error",
      signal: controller.signal
    });
    // Consume the body within the timeout, but never log or return it.
    let data = null;
    try { data = await response.json(); } catch { /* A non-JSON response is a failure. */ }
    return { status: response.status, data };
  } finally {
    clearTimeout(timeout);
  }
}

async function applyLabels(number, labels, token) {
  try {
    const existing = [];
    for (const label of labels) {
      const response = await github(`/labels/${encodeURIComponent(label)}`, token);
      if (response.status === 200 && response.data?.name === label && !response.data.archived_at) existing.push(label);
      else console.warn("report_label_unavailable");
    }
    if (!existing.length) return;
    const response = await github(`/issues/${number}/labels`, token, "POST", { labels: existing });
    const applied = Array.isArray(response.data) ? response.data.map((label) => label?.name) : [];
    if (response.status !== 200 || existing.some((label) => !applied.includes(label))) console.warn("report_labels_incomplete");
  } catch {
    // The issue already exists. Never repeat its creation for a label failure.
    console.warn("report_labels_incomplete");
  }
}

export default {
  async fetch(request, env, ctx) {
    const path = new URL(request.url).pathname;
    if (path === "/health" && request.method === "GET") return json({ ok: true }, 200);
    if (path !== "/api/report") return json({ ok: false, error: "not_found", message: "Endpoint not found." }, 404);
    const origin = allowedOrigin(request, env);
    if (!origin) return json({ ok: false, error: "origin_not_allowed", message: "This origin is not allowed." }, 403);
    if (request.method === "OPTIONS") {
      const method = request.headers.get("Access-Control-Request-Method");
      const headers = (request.headers.get("Access-Control-Request-Headers") || "").split(",").map((value) => value.trim().toLowerCase()).filter(Boolean);
      if (method !== "POST" || headers.some((header) => header !== "content-type")) {
        return json({ ok: false, error: "preflight_rejected", message: "This preflight request is not supported." }, 403, origin);
      }
      return new Response(null, { status: 204, headers: {
        "Access-Control-Allow-Origin": origin,
        "Access-Control-Allow-Methods": "POST",
        "Access-Control-Allow-Headers": "Content-Type",
        "Access-Control-Max-Age": "600",
        Vary: "Origin, Access-Control-Request-Method, Access-Control-Request-Headers"
      } });
    }
    if (request.method !== "POST") return json({ ok: false, error: "method_not_allowed", message: "Use POST for submissions." }, 405, origin, { Allow: "POST, OPTIONS" });
    if (env.SUBMISSIONS_ENABLED !== "true") return json({ ok: false, error: "submissions_disabled", message: "Online submissions are not enabled yet." }, 503, origin);
    try {
      const input = await readReport(request);
      const report = normalizeReport(input);
      const verified = await verifyTurnstile(input.turnstileToken, input.type, env.TURNSTILE_SECRET_KEY);
      // Discard the transient token before creating any GitHub request.
      delete input.turnstileToken;
      if (!verified) {
        console.warn("report_verification_failed");
        return json({ ok: false, error: "verification_failed", message: "Verification failed. Please try again." }, 403, origin);
      }
      if (typeof env.GITHUB_TOKEN !== "string" || !env.GITHUB_TOKEN.trim() || env.GITHUB_TOKEN === "replace-me") {
        console.error("report_configuration_unavailable");
        return json({ ok: false, error: "submission_failed", message: FAILURE_MESSAGE }, 503, origin);
      }
      // Create exactly once, without labels. Labelling cannot block this operation.
      const response = await github("/issues", env.GITHUB_TOKEN, "POST", { title: report.title, body: report.body });
      const number = response.data?.number;
      if (response.status !== 201 || !Number.isSafeInteger(number) || number < 1 || response.data?.pull_request) {
        console.error("report_creation_failed");
        return json({ ok: false, error: "submission_failed", message: FAILURE_MESSAGE }, 502, origin);
      }
      try {
        ctx.waitUntil(applyLabels(number, report.labels, env.GITHUB_TOKEN));
      } catch {
        // Even a scheduling failure must not turn an existing issue into a retry.
        console.warn("report_labels_incomplete");
      }
      return json({ ok: true, requestNumber: number, status: "received" }, 201, origin);
    } catch (error) {
      if (error instanceof ValidationError) {
        return json({ ok: false, error: "validation_error", message: error.message, ...(error.field ? { field: error.field } : {}) }, error.status, origin);
      }
      console.error("report_creation_failed");
      return json({ ok: false, error: "submission_failed", message: FAILURE_MESSAGE }, 502, origin);
    }
  }
};
