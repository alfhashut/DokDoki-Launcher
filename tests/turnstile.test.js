import { assert, equal, test, until } from "./helpers.js";
import { withPage, fill, choose, submit, formValues, localAPI } from "./website.test.js";

const success = { status: 201, data: { ok: true, requestNumber: 43, status: "received" } };
const token = "XXXX.DUMMY.TOKEN.XXXX";
const scriptURL = "https://challenges.cloudflare.com/turnstile/v0/api.js?render=explicit";
const scripts = (doc) => [...doc.scripts].filter((script) => script.src === scriptURL);

test("Turnstile client: disabled/incomplete configuration never loads or renders a widget", async () => {
  for (const params of [{ api: "" }, { api: localAPI, submissions: "0" }, { api: localAPI, sitekey: "" }, { submissions: "1", sitekey: "1x00000000000000000000AA" }]) {
    await withPage("requests.html", [], async ({ doc, fixture }) => {
      equal(fixture.scriptLoads.length, 0);
      equal(fixture.sdk.renders.length, 0);
      assert([...doc.querySelectorAll("button[type=submit]")].every((button) => button.disabled));
      assert([...doc.querySelectorAll(".verification")].every((node) => node.hidden));
    }, params);
  }
});

test("Turnstile client: official script, theme auto, actions, no hidden token fields", async () => {
  await withPage("requests.html", [], async ({ win, doc, fixture }) => {
    equal(fixture.scriptLoads, ["https://challenges.cloudflare.com/turnstile/v0/api.js?render=explicit"]);
    for (const [form, action] of [["bug-form", "report_bug"], ["mod-form", "request_mod"], ["removal-form", "request_removal"]]) {
      choose(win, "request-type", form);
      const widget = [...fixture.sdk.widgets.values()].find((item) => item.form === form);
      equal(widget.options.action, action);
      assert(/^[a-zA-Z0-9_-]{1,32}$/.test(action));
      equal(widget.options.theme, "auto");
      equal(widget.options["response-field"], false);
      equal(fixture.sdk.widgets.size, 1, "Hidden forms must not retain widgets");
      equal(fixture.scriptLoads.length, 1);
      equal(scripts(doc).length, 1);
      equal(fixture.sdk.readyCalls, 0);
      assert(!doc.querySelector('input[name="cf-turnstile-response"]'));
    }
  }, { api: localAPI });
});

test("Turnstile client: no submission before challenge success, even with synthetic submit", async () => {
  await withPage("requests.html", [success], async ({ win, doc, fixture }) => {
    fill(win, formValues["bug-form"]);
    assert(doc.getElementById("bug-form").querySelector("button[type=submit]").disabled);
    assert(doc.querySelector(".verification-message").textContent.includes("Checking"));
    submit(win, "bug-form");
    equal(fixture.calls.length, 0);
    fixture.sdk.solve("bug-form");
    assert(!doc.getElementById("bug-form").querySelector("button[type=submit]").disabled);
    submit(win, "bug-form");
    await until(() => doc.getElementById("bug-form-result").textContent.includes("Request #43"));
    equal(JSON.parse(fixture.calls[0].body).turnstileToken, token);
    equal(fixture.sdk.widgets.size, 0);
    assert(!doc.getElementById("bug-form-result").textContent.includes(token));
    assert(!doc.getElementById("bug-form-result").querySelector("a").href.includes(token));
  }, { api: localAPI }, { autoSolve: false });
});

for (const response of [
  { status: 403, data: { ok: false, error: "verification_failed", message: "do-not-expose-details" } },
  { status: 400, data: { ok: false, error: "validation_error", message: "Please check the report." } },
  { status: 502, data: { ok: false, error: "submission_failed" } },
  { offline: true }, { timeout: true }, { raw: "malformed JSON" }
]) {
  test(`Turnstile client: failure consumes local token and requires fresh verification (${JSON.stringify(response)})`, async () => {
    await withPage("requests.html", [response, success], async ({ win, doc, fixture }) => {
      const widget = [...fixture.sdk.widgets.values()][0];
      fixture.sdk.autoSolve = false;
      fill(win, formValues["bug-form"]);
      submit(win, "bug-form");
      await until(() => !doc.getElementById("bug-form").hasAttribute("aria-busy"));
      assert(fixture.sdk.resets.includes(widget.id));
      assert(doc.getElementById("bug-form").querySelector("button[type=submit]").disabled);
      assert(!doc.getElementById("bug-form-result").textContent.includes("do-not-expose-details"));
      submit(win, "bug-form");
      equal(fixture.calls.length, 1);
      fixture.sdk.solve("bug-form");
      submit(win, "bug-form");
      await until(() => doc.getElementById("bug-form-result").textContent.includes("Request #43"));
      // A stale widget callback cannot unlock a successful form or leak a token.
      widget.options.callback(token);
      submit(win, "bug-form");
      equal(fixture.calls.length, 2);
      assert(doc.getElementById("bug-form").querySelector("button[type=submit]").disabled);
      equal(fixture.sdk.widgets.size, 0);
    }, { api: localAPI });
  });
}

for (const callback of ["expired-callback", "timeout-callback"]) {
  test(`Turnstile client: ${callback} clears token, resets challenge and recovers`, async () => {
    await withPage("requests.html", [], async ({ win, doc, fixture }) => {
      fixture.sdk.autoSolve = false;
      const widget = [...fixture.sdk.widgets.values()][0];
      widget.options[callback]();
      equal(fixture.sdk.resets, [widget.id]);
      assert(doc.querySelector(".verification-message").textContent.includes("Verification expired"));
      fill(win, formValues["bug-form"]);
      submit(win, "bug-form");
      equal(fixture.calls.length, 0);
      fixture.sdk.solve("bug-form");
      assert(!doc.getElementById("bug-form").querySelector("button[type=submit]").disabled);
    }, { api: localAPI });
  });
}

test("Turnstile client: delayed expiry callback cannot send a token older than five minutes", async () => {
  await withPage("requests.html", [], async ({ win, doc, fixture }) => {
    fixture.sdk.autoSolve = false;
    fill(win, formValues["bug-form"]);
    const now = win.Date.now;
    win.Date.now = () => now() + 300001;
    submit(win, "bug-form");
    equal(fixture.calls.length, 0);
    equal(fixture.sdk.resets.length, 1);
    assert(doc.querySelector(".verification-message").textContent.includes("expired"));
    win.Date.now = now;
  }, { api: localAPI });
});

test("Turnstile client: script failure retains the singleton and Retry never reloads it", async () => {
  await withPage("requests.html", [], async ({ win, doc, fixture }) => {
    await until(() => doc.querySelector(".verification-message").textContent.includes("Verification failed"));
    const retry = doc.querySelector(".verification button");
    assert(!retry.hidden && retry.type === "button");
    assert(doc.getElementById("bug-form").querySelector("button[type=submit]").disabled);
    const failed = win.DOKDOKI_TEST_LOAD_TURNSTILE();
    for (let i = 0; i < 3; i += 1) {
      retry.click();
      assert(win.DOKDOKI_TEST_LOAD_TURNSTILE() === failed);
    }
    await until(() => !retry.hidden);
    assert(doc.querySelector(".verification-message").textContent.includes("Reload this page"));
    equal(scripts(doc).length, 1);
    equal(fixture.scriptLoads.length, 1);
    equal(fixture.sdk.renders.length, 0);
    assert(doc.getElementById("bug-form").querySelector("button[type=submit]").disabled);
    equal(fixture.calls.length, 0);
  }, { api: localAPI }, { scriptError: true });
});

test("Turnstile client: widget error can be retried without an automatic error loop", async () => {
  await withPage("requests.html", [], async ({ doc, fixture }) => {
    const widget = [...fixture.sdk.widgets.values()][0];
    fixture.sdk.autoSolve = false;
    widget.options["error-callback"]("110200");
    assert(doc.querySelector(".verification-message").textContent.includes("Verification failed"));
    assert(!doc.body.textContent.includes("110200"));
    equal(fixture.consoleWarnings, [["Turnstile client error:", "110200"]]);
    equal(fixture.sdk.resets.length, 0);
    const retry = doc.querySelector(".verification").querySelectorAll("button")[1];
    retry.click();
    equal(fixture.sdk.resets.length, 1);
    equal(fixture.scriptLoads.length, 1);
    equal(scripts(doc).length, 1);
    equal(fixture.sdk.widgets.size, 1);
    fixture.sdk.solve("bug-form");
    assert(!doc.getElementById("bug-form").querySelector("button[type=submit]").disabled);
  }, { api: localAPI });
});

test("Turnstile client: concurrent loads, retries and form changes share one promise and script", async () => {
  await withPage("requests.html", [], async ({ win, doc, fixture }) => {
    const first = win.DOKDOKI_TEST_LOAD_TURNSTILE();
    assert(first === win.DOKDOKI_TEST_LOAD_TURNSTILE(), "Promise identity must be shared");
    for (const form of ["mod-form", "removal-form", "bug-form"]) {
      choose(win, "request-type", form);
      doc.getElementById(form).querySelector(".verification button").click();
      assert(first === win.DOKDOKI_TEST_LOAD_TURNSTILE());
    }
    equal(fixture.sdk.renders.length, 0);
    equal(fixture.scriptLoads.length, 1);
    equal(scripts(doc).length, 1);
    equal(scripts(doc)[0].id, "dokdoki-turnstile-api");
    // The SDK may expose its global immediately before its script load event.
    win.turnstile = fixture.sdk;
    assert(first === win.DOKDOKI_TEST_LOAD_TURNSTILE(), "Do not replace an in-flight promise");
    equal(fixture.sdk.renders.length, 0);
    fixture.releaseScript();
    assert(await first === fixture.sdk);
    await until(() => fixture.sdk.widgets.size === 1);
    assert(first === win.DOKDOKI_TEST_LOAD_TURNSTILE());
    equal(fixture.sdk.readyCalls, 0);
    equal(fixture.sdk.renders.length, 1);
    equal(fixture.calls.length, 0);
  }, {}, { holdScript: true, autoSolve: false });
});

test("Turnstile client: preloaded API is reused without inserting any SDK script", async () => {
  await withPage("requests.html", [], async ({ win, doc, fixture }) => {
    const first = win.DOKDOKI_TEST_LOAD_TURNSTILE();
    assert(await first === fixture.sdk);
    assert(first === win.DOKDOKI_TEST_LOAD_TURNSTILE());
    equal(fixture.scriptLoads.length, 0);
    equal(scripts(doc).length, 0);
    equal(fixture.sdk.widgets.size, 1);
    equal(fixture.sdk.readyCalls, 0);
    for (const form of ["mod-form", "removal-form", "bug-form"]) choose(win, "request-type", form);
    equal(fixture.sdk.widgets.size, 1);
    equal(fixture.scriptLoads.length, 0);
  }, {}, { existingAPI: true, autoSolve: false });
});

for (const existingScript of [true, "id"]) {
  test(`Turnstile client: an existing SDK script is reused (${existingScript})`, async () => {
    await withPage("requests.html", [], async ({ win, doc, fixture }) => {
      const pending = win.DOKDOKI_TEST_LOAD_TURNSTILE();
      equal(scripts(doc).length, 1);
      assert(scripts(doc)[0] === fixture.existingScriptElement);
      equal(fixture.scriptLoads.length, 0);
      equal(fixture.sdk.renders.length, 0);
      fixture.releaseScript();
      assert(await pending === fixture.sdk);
      await until(() => fixture.sdk.widgets.size === 1);
      equal(fixture.scriptLoads.length, 0);
      equal(fixture.sdk.readyCalls, 0);
    }, {}, { existingScript, holdScript: true, autoSolve: false });
  });
}

test("Turnstile client: timeout never reinserts the SDK; Retry can reuse an API that arrived late", async () => {
  await withPage("requests.html", [], async ({ win, doc, fixture }) => {
    const retry = doc.querySelector(".verification button");
    await until(() => !retry.hidden);
    const failed = win.DOKDOKI_TEST_LOAD_TURNSTILE();
    retry.click();
    assert(failed === win.DOKDOKI_TEST_LOAD_TURNSTILE());
    await until(() => !retry.hidden);
    equal(fixture.sdk.renders.length, 0);
    fixture.releaseScript();
    retry.click();
    await until(() => fixture.sdk.widgets.size === 1);
    equal(scripts(doc).length, 1);
    equal(fixture.scriptLoads.length, 1);
    equal(fixture.sdk.readyCalls, 0);
    assert(doc.getElementById("bug-form").querySelector("button[type=submit]").disabled);
  }, {}, { holdScript: true, scriptTimeout: true, autoSolve: false });
});

test("Turnstile client: loaded script without an API stays safely locked without reinsertion", async () => {
  await withPage("requests.html", [], async ({ win, doc, fixture }) => {
    const retry = doc.querySelector(".verification button");
    await until(() => !retry.hidden);
    retry.click();
    await until(() => !retry.hidden);
    equal(fixture.scriptLoads.length, 1);
    equal(fixture.sdk.renders.length, 0);
    equal(scripts(doc).length, 1);
    assert(doc.getElementById("bug-form").querySelector("button[type=submit]").disabled);
    win.turnstile = fixture.sdk;
    retry.click();
    await until(() => fixture.sdk.widgets.size === 1);
    equal(fixture.scriptLoads.length, 1);
  }, {}, { missingAPI: true, autoSolve: false });
});

test("Turnstile client: failed widget render retries through the existing API", async () => {
  await withPage("requests.html", [], async ({ doc, fixture }) => {
    const retry = doc.querySelector(".verification button");
    await until(() => !retry.hidden);
    equal(fixture.sdk.widgets.size, 0);
    fixture.renderError = false;
    retry.click();
    equal(fixture.sdk.widgets.size, 1);
    equal(fixture.scriptLoads.length, 1);
    equal(scripts(doc).length, 1);
    equal(fixture.sdk.readyCalls, 0);
  }, {}, { renderError: true, autoSolve: false });
});

test("Turnstile client: diagnostics reject arbitrary error payloads and never log form data or tokens", async () => {
  await withPage("requests.html", [], async ({ win, doc, fixture }) => {
    fill(win, formValues["bug-form"]);
    const widget = [...fixture.sdk.widgets.values()][0];
    for (const code of [token, { token }, "form content", "110200\n", "110200\nsecret", 110200]) widget.options["error-callback"](code);
    equal(fixture.consoleWarnings, []);
    widget.options["error-callback"]("300030");
    equal(fixture.consoleWarnings, [["Turnstile client error:", "300030"]]);
    assert(doc.getElementById("bug-form").querySelector("button[type=submit]").disabled);
    equal(fixture.calls.length, 0);
  }, {}, { autoSolve: false });
});

test("Turnstile client: only the conditional loader owns api.js and never calls ready()", async () => {
  const source = await (await fetch("/DokDoki-Launcher/js/requests.js")).text();
  const html = await (await fetch("/DokDoki-Launcher/requests.html")).text();
  assert(!source.includes("turnstile.ready("));
  assert(!source.includes("DOKDOKI_TEST_LOAD_TURNSTILE"), "Test hook must never exist in production source");
  assert(!html.includes(scriptURL));
  equal(source.split(scriptURL).length - 1, 1);
});

test("Turnstile client: switching forms invalidates previous widget callbacks", async () => {
  await withPage("requests.html", [], async ({ win, doc, fixture }) => {
    fixture.sdk.autoSolve = false;
    for (const form of ["mod-form", "removal-form", "bug-form"]) {
      const previous = [...fixture.sdk.widgets.values()][0];
      choose(win, "request-type", form);
      previous.options.callback(token);
      assert([...doc.querySelectorAll("button[type=submit]")].every((button) => button.disabled));
      equal(fixture.sdk.widgets.size, 1);
      fixture.sdk.solve(form);
      assert(!doc.getElementById(form).querySelector("button[type=submit]").disabled);
      equal(fixture.scriptLoads.length, 1);
    }
  }, { api: localAPI });
});

test("Turnstile client: back/forward cache clears the token and requires a new widget", async () => {
  await withPage("requests.html", [], async ({ win, doc, fixture }) => {
    const previous = [...fixture.sdk.widgets.values()][0];
    fixture.sdk.autoSolve = false;
    win.dispatchEvent(new win.PageTransitionEvent("pagehide", { persisted: true }));
    equal(fixture.sdk.widgets.size, 0);
    previous.options.callback(token);
    assert(doc.getElementById("bug-form").querySelector("button[type=submit]").disabled);
    win.dispatchEvent(new win.PageTransitionEvent("pageshow", { persisted: true }));
    equal(fixture.sdk.widgets.size, 1);
    assert(doc.getElementById("bug-form").querySelector("button[type=submit]").disabled);
    equal(fixture.calls.length, 0);
  }, { api: localAPI });
});
