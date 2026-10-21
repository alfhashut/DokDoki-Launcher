import { assert, equal, test, until } from "./helpers.js";
import { exercise } from "./worker.test.js";

window.DOKDOKI_TEST_CASES = new Map();
let sequence = 0;
export const localAPI = "http://127.0.0.1:8787";
const publicAPI = "https://api.github.com/repos/alfhashut/DokDoki-Launcher";

export async function withPage(name, responses, run, params = {}, options = {}) {
  const key = String(++sequence);
  const fixture = { responses, ...options };
  window.DOKDOKI_TEST_CASES.set(key, fixture);
  const frame = document.createElement("iframe");
  frame.width = "375";
  frame.height = "700";
  frame.title = `Test ${sequence}: ${name}`;
  const url = new URL(`/DokDoki-Launcher/${name}`, location.origin);
  const testConfig = params.api && !params._manual ? { submissions: "1", sitekey: "1x00000000000000000000AA" } : {};
  url.search = new URLSearchParams({ _test: key, ...testConfig, ...params });
  frame.src = url.href;
  const loaded = new Promise((resolve) => frame.addEventListener("load", resolve, { once: true }));
  document.getElementById("frames").append(frame);
  try {
    await Promise.race([loaded, new Promise((_, reject) => setTimeout(() => reject(new Error("Frame load timed out")), 4000))]);
    const win = frame.contentWindow;
    const doc = win.document;
    if (name !== "requests.html") {
      await until(() => !doc.querySelector("#release-message, #status-message").textContent.startsWith("Loading"));
    }
    await run({ win, doc, fixture, frame });
  } finally {
    frame.remove();
    window.DOKDOKI_TEST_CASES.delete(key);
  }
}

export function fill(win, values) {
  for (const [id, value] of Object.entries(values)) {
    const field = win.document.getElementById(id);
    field.value = value;
    field.dispatchEvent(new win.Event("input", { bubbles: true }));
  }
}
export function choose(win, id, value) {
  const select = win.document.getElementById(id);
  select.value = value;
  select.dispatchEvent(new win.Event("change", { bubbles: true }));
}
export function submit(win, id) {
  const event = new win.Event("submit", { cancelable: true, bubbles: true });
  win.document.getElementById(id).dispatchEvent(event);
  assert(event.defaultPrevented, "Native submission must be prevented");
}
export const formValues = {
  "bug-form": { "bug-version": "v9.0.0", "bug-os": "windows", "bug-happened": "The launcher closes on startup.", "bug-steps": "Open the launcher and choose a profile." },
  "mod-form": { "mod-name": "Example mod", "mod-url": "https://example.com/mod" },
  "removal-form": { "removal-name": "Example mod", "removal-url": "https://example.com/mod", "removal-reason": "This listing should be removed.", "removal-rights": "no" }
};

test("Website: production defaults stay disabled and submit events send nothing", async () => {
  await withPage("requests.html", [], async ({ win, doc, fixture }) => {
    for (const form of Object.keys(formValues)) {
      choose(win, "request-type", form);
      fill(win, formValues[form]);
      submit(win, form);
    }
    assert([...doc.querySelectorAll("button[type=submit]")].every((button) => button.disabled));
    equal(fixture.calls.length, 0);
    assert(doc.getElementById("submission-notice").textContent.includes("unavailable"));
  });
});

test("Website: production ignores a supplied API query parameter", async () => {
  const source = await (await fetch("/DokDoki-Launcher/js/requests.js")).text();
  assert(source.includes('const API_BASE_URL = "";'), "Production API URL must remain unset");
  await withPage("requests.html", [], async ({ win, doc, fixture }) => {
    const run = new win.Function("window", source);
    run({ location: { hostname: "alfhashut.github.io", search: "?submissions=1&sitekey=1x00000000000000000000AA&api=https%3A%2F%2Fevil.example" }, addEventListener: win.addEventListener.bind(win) });
    fill(win, formValues["bug-form"]);
    submit(win, "bug-form");
    assert([...doc.querySelectorAll("button[type=submit]")].every((button) => button.disabled));
    equal(fixture.calls.length, 0);
  }, { _manual: "1", api: "https://evil.example" });
});

for (const api of ["javascript:alert(1)", "http://public.example", "https://user:pass@example.com", "https://example.com/?token=not-used", "https://example.com/#fragment"]) {
  test(`Website: unsafe API configuration remains disabled (${api})`, async () => {
    await withPage("requests.html", [], async ({ doc, fixture }) => {
      assert([...doc.querySelectorAll("button[type=submit]")].every((button) => button.disabled));
      equal(fixture.calls.length, 0);
    }, { api });
  });
}

for (const [id, values] of Object.entries(formValues)) {
  test(`Website: ${id} posts only intended JSON and shows an internal success link`, async () => {
    await withPage("requests.html", [{ status: 201, data: { ok: true, requestNumber: 43, status: "received" } }], async ({ win, doc, fixture }) => {
      assert(doc.getElementById("submission-notice").textContent.includes("publicly visible"));
      choose(win, "request-type", id);
      fill(win, values);
      submit(win, id);
      await until(() => doc.getElementById(`${id}-result`).textContent.includes("Request #43"));
      equal(fixture.calls.length, 1);
      const call = fixture.calls[0];
      equal(call.url, localAPI + "/api/report");
      equal(call.method, "POST");
      equal(call.credentials, "omit");
      equal(call.headers["Content-Type"], "application/json");
      const payload = JSON.parse(call.body);
      const expectedKeys = id === "bug-form" ? ["type", "dokdokiVersion", "operatingSystem", "whatHappened", "stepsToReproduce", "additionalInformation"]
        : id === "mod-form" ? ["type", "modName", "sourceUrl", "modAuthor", "modVersion", "additionalInformation"]
          : ["type", "modName", "sourceUrl", "reason", "rightsHolder", "proofUrl", "additionalInformation"];
      expectedKeys.push("turnstileToken");
      equal(payload.turnstileToken, "XXXX.DUMMY.TOKEN.XXXX");
      equal(Object.keys(payload), expectedKeys);
      equal(payload.type, id === "bug-form" ? "bug" : id === "mod-form" ? "mod-request" : "removal-request");
      const result = doc.getElementById(`${id}-result`);
      assert(result.textContent.includes("Report submitted") && result.textContent.includes("Status: Received"));
      const link = new URL(result.querySelector("a").href);
      equal(link.origin, location.origin);
      equal(link.pathname, "/DokDoki-Launcher/status.html");
      equal(link.search, "?q=43");
      assert(!result.querySelector('a[href*="github.com"]'));
      submit(win, id);
      equal(fixture.calls.length, 1, "Successful form must not resubmit");
      assert(doc.getElementById(id).querySelector("button[type=submit]").disabled);
    }, { api: localAPI });
  });
}

test("Website: duplicate clicks and cross-form submissions are locked while sending", async () => {
  await withPage("requests.html", [{ hold: true, status: 201, data: { ok: true, requestNumber: 43, status: "received" } }], async ({ win, doc, fixture }) => {
    fill(win, formValues["bug-form"]);
    fill(win, formValues["mod-form"]);
    submit(win, "bug-form");
    submit(win, "bug-form");
    submit(win, "mod-form");
    equal(fixture.calls.length, 1);
    assert([...doc.querySelectorAll("button[type=submit]")].every((button) => button.disabled));
    assert(doc.getElementById("request-type").disabled);
    equal(doc.getElementById("bug-form-result").textContent, "Submitting…");
    await until(() => fixture.release);
    fixture.release();
    await until(() => doc.getElementById("bug-form-result").textContent.includes("Request #43"));
    assert(!doc.getElementById("request-type").disabled);
  }, { api: localAPI });
});

for (const [id, values] of Object.entries(formValues)) {
  test(`Integration: ${id} goes through the actual Worker with mocked GitHub`, async () => {
    let workerResult;
    await withPage("requests.html", [{ handler: async (url, options) => {
      workerResult = await exercise(JSON.parse(options.body), { origin: location.origin, env: { ALLOWED_ORIGINS: location.origin } });
      return new Response(workerResult.text, { status: workerResult.status, headers: workerResult.headers });
    } }], async ({ win, doc, fixture }) => {
      equal(fixture.calls.length, 0, "Loading an enabled form must never auto-submit");
      choose(win, "request-type", id);
      fill(win, values);
      submit(win, id);
      await until(() => doc.getElementById(`${id}-result`).textContent.includes("Request #43"));
      equal(workerResult.status, 201);
      equal(workerResult.calls.filter((call) => call.url.endsWith("/issues")).length, 1);
    }, { api: localAPI });
  });
}

test("Website: local validation rejects whitespace, short descriptions, and unsafe URLs", async () => {
  await withPage("requests.html", [], async ({ win, doc, fixture }) => {
    fill(win, { ...formValues["bug-form"], "bug-version": "   ", "bug-happened": "short" });
    submit(win, "bug-form");
    assert(!doc.getElementById("bug-version").validity.valid && !doc.getElementById("bug-happened").validity.valid);
    choose(win, "request-type", "mod-form");
    fill(win, { ...formValues["mod-form"], "mod-url": "javascript:alert(1)" });
    submit(win, "mod-form");
    assert(!doc.getElementById("mod-url").validity.valid);
    equal(fixture.calls.length, 0);
  }, { api: localAPI });
});

test("Website: backend validation message is safely rendered and retry can succeed", async () => {
  await withPage("requests.html", [{ status: 400, data: { ok: false, error: "validation_error", message: "Please check <the version>.", field: "dokdokiVersion" } }, { status: 201, data: { ok: true, requestNumber: 44, status: "received" } }], async ({ win, doc, fixture }) => {
    fill(win, formValues["bug-form"]);
    submit(win, "bug-form");
    await until(() => doc.getElementById("bug-version").getAttribute("aria-invalid") === "true");
    assert(doc.getElementById("bug-form-result").textContent.includes("<the version>"));
    assert(!doc.getElementById("bug-form-result").querySelector("the"));
    assert(!doc.getElementById("bug-form").querySelector("button[type=submit]").disabled);
    fill(win, { "bug-version": "v9.0.1" });
    submit(win, "bug-form");
    await until(() => doc.getElementById("bug-form-result").textContent.includes("Request #44"));
    equal(fixture.calls.length, 2);
  }, { api: localAPI });
});

for (const response of [
  { offline: true }, { timeout: true }, { raw: "not JSON" },
  { status: 502, data: { ok: false, error: "submission_failed", message: "do-not-expose-upstream-details" } },
  { status: 503, data: { ok: false, error: "submissions_disabled" } },
  { status: 201, data: { ok: true, requestNumber: "43", status: "received" } }
]) {
  test(`Website: handles failure without fake success or raw upstream details (${JSON.stringify(response)})`, async () => {
    await withPage("requests.html", [response], async ({ win, doc, fixture }) => {
      fill(win, formValues["bug-form"]);
      submit(win, "bug-form");
      await until(() => !doc.getElementById("bug-form").hasAttribute("aria-busy"));
      const result = doc.getElementById("bug-form-result");
      assert(result.classList.contains("error"));
      assert(!result.textContent.includes("Request #") && !result.textContent.includes("do-not-expose"));
      equal(fixture.calls.length, 1);
      assert(!doc.getElementById("bug-form").querySelector("button[type=submit]").disabled);
    }, { api: localAPI });
  });
}

const issues = [
  { number: 43, title: "A profile bug", state: "open", labels: [{ name: "bug" }, { name: "status:received" }], updated_at: "2026-10-21T12:00:00Z", html_url: "https://github.com/alfhashut/DokDoki-Launcher/issues/43" },
  { number: 143, title: "A mod request", state: "closed", labels: ["mod-request", "status:completed"], updated_at: "2026-10-20T12:00:00Z" },
  { number: 2, title: "<img src=x onerror=alert(1)>", state: "open", labels: [], updated_at: "2026-10-19T12:00:00Z", html_url: "javascript:alert(1)" },
  { number: 1, title: "Pull request must be excluded", pull_request: {}, labels: [] }
];

test("Website: status q=NUMBER applies exact lookup and manual search still works", async () => {
  await withPage("status.html", [{ data: issues }], async ({ win, doc, fixture }) => {
    equal(doc.getElementById("request-search").value, "43");
    equal(doc.querySelectorAll("#issue-list h2").length, 1);
    assert(doc.querySelector("#issue-list h2").textContent.includes("#43 "));
    assert(doc.getElementById("issue-list").textContent.includes("Received"));
    fill(win, { "request-search": "MOD REQUEST" });
    equal(doc.querySelectorAll("#issue-list h2").length, 1);
    assert(doc.getElementById("issue-list").textContent.includes("Completed"));
    fill(win, { "request-search": "" });
    equal(doc.querySelectorAll("#issue-list h2").length, 3);
    assert(!doc.querySelector("#issue-list img") && !doc.querySelector('a[href^="javascript:"]'));
    assert(fixture.calls[0].url.startsWith(publicAPI + "/issues?") && fixture.calls[0].url.includes("state=all"));
    equal(fixture.calls.length, 1);
  }, { q: "43" });
});

test("Website: new request not visible yet is a temporary status, not an API failure", async () => {
  await withPage("status.html", [{ data: [] }], async ({ doc }) => {
    assert(doc.getElementById("issue-list").textContent.includes("Request #43 is not visible"));
    assert(doc.getElementById("issue-list").textContent.includes("may take a moment"));
    assert(!doc.getElementById("status-message").classList.contains("error"));
  }, { q: "43" });
});

test("Website: Phase 1 status pagination and failures preserve searchable requests", async () => {
  await withPage("status.html", [{ data: issues.slice(0, 1), next: true }, { status: 500, data: {} }, { data: issues.slice(1) }], async ({ win, doc, fixture }) => {
    doc.getElementById("status-load-more").click();
    await until(() => !doc.getElementById("status-retry").hidden);
    fill(win, { "request-search": "#43" });
    equal(doc.querySelectorAll("#issue-list h2").length, 1);
    doc.getElementById("status-retry").click();
    await until(() => doc.getElementById("status-message").textContent.includes("All available"));
    fill(win, { "request-search": "143" });
    assert(doc.getElementById("issue-list").textContent.includes("#143 "));
    equal(fixture.calls[1].url, fixture.calls[2].url);
  });
});

const asset = (name, tag = "v9.0.0") => ({ name, browser_download_url: `https://github.com/alfhashut/DokDoki-Launcher/releases/download/${tag}/${name}` });
const stable = { tag_name: "v9.0.0", published_at: "2026-10-20T12:00:00Z", prerelease: false, assets: [asset("DokDoki-Launcher.exe"), asset("DokDoki-Launcher-Linux-x86_64.AppImage"), asset("SHA256SUMS.txt")] };
const preview = { tag_name: "v10-beta", published_at: "2026-10-21T12:00:00Z", prerelease: true, assets: [asset("DokDoki-Launcher.exe", "v10-beta")] };

test("Website: Phase 1 releases paginate, exclude drafts, prefer stable, and update assets", async () => {
  await withPage("downloads.html", [{ data: [preview, { ...stable, draft: true }], next: true }, { data: [stable] }], async ({ win, doc, fixture }) => {
    equal(doc.querySelectorAll("#release-select option").length, 2);
    assert(doc.querySelector("#release-select option").textContent.includes("v9.0.0 · Latest stable"));
    assert(doc.querySelector("#release-checksums a").href.endsWith("SHA256SUMS.txt"));
    choose(win, "release-select", "1");
    assert(!doc.getElementById("prerelease-notice").hidden);
    equal(doc.querySelectorAll("#release-assets a").length, 1);
    assert(doc.querySelector("#release-assets a").href.includes("/v10-beta/"));
    assert(fixture.calls[1].url.endsWith("page=2"));
  });
});

test("Website: Phase 1 home dynamically uses the latest stable assets", async () => {
  await withPage("index.html", [{ data: [preview, stable] }], async ({ doc }) => {
    equal(doc.getElementById("release-version").textContent, "v9.0.0");
    equal(doc.querySelectorAll("#home-downloads a").length, 2);
  });
});

test("Website: Phase 1 release errors remain recoverable", async () => {
  await withPage("downloads.html", [{ status: 403, data: {} }, { data: [stable] }], async ({ doc }) => {
    assert(doc.getElementById("release-message").textContent.includes("limiting requests"));
    doc.getElementById("release-retry").click();
    await until(() => !doc.getElementById("release-select").disabled);
    equal(doc.getElementById("release-version").textContent, "v9.0.0");
  });
});
