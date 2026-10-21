import { assert, equal, test, until } from "./helpers.js";
import { withPage, fill, choose, submit, formValues, localAPI } from "./website.test.js";

const success = { status: 201, data: { ok: true, requestNumber: 43, status: "received" } };
const token = "XXXX.DUMMY.TOKEN.XXXX";

test("Turnstile client: disabled/incomplete configuration never loads or renders a widget", async () => {
  for (const params of [{}, { api: localAPI, submissions: "0" }, { api: localAPI, sitekey: "" }, { submissions: "1", sitekey: "1x00000000000000000000AA" }]) {
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

test("Turnstile client: script load failure stays locked and offers an accessible retry", async () => {
  await withPage("requests.html", [], async ({ doc, fixture }) => {
    await until(() => doc.querySelector(".verification-message").textContent.includes("Verification failed"));
    const retry = doc.querySelector(".verification button");
    assert(!retry.hidden && retry.type === "button");
    assert(doc.getElementById("bug-form").querySelector("button[type=submit]").disabled);
    fixture.scriptError = false;
    retry.click();
    await until(() => !doc.getElementById("bug-form").querySelector("button[type=submit]").disabled);
    equal(fixture.scriptLoads.length, 2);
    equal(fixture.calls.length, 0);
  }, { api: localAPI }, { scriptError: true });
});

test("Turnstile client: widget error can be retried without an automatic error loop", async () => {
  await withPage("requests.html", [], async ({ doc, fixture }) => {
    const widget = [...fixture.sdk.widgets.values()][0];
    fixture.sdk.autoSolve = false;
    widget.options["error-callback"]("raw-error-code");
    assert(doc.querySelector(".verification-message").textContent.includes("Verification failed"));
    assert(!doc.body.textContent.includes("raw-error-code"));
    equal(fixture.sdk.resets.length, 0);
    const retry = doc.querySelector(".verification").querySelectorAll("button")[1];
    retry.click();
    equal(fixture.sdk.resets.length, 1);
    fixture.sdk.solve("bug-form");
    assert(!doc.getElementById("bug-form").querySelector("button[type=submit]").disabled);
  }, { api: localAPI });
});

test("Turnstile client: switching forms invalidates previous widget callbacks", async () => {
  await withPage("requests.html", [], async ({ win, doc, fixture }) => {
    const previous = [...fixture.sdk.widgets.values()][0];
    fixture.sdk.autoSolve = false;
    choose(win, "request-type", "mod-form");
    previous.options.callback(token);
    assert(doc.getElementById("bug-form").querySelector("button[type=submit]").disabled);
    assert(doc.getElementById("mod-form").querySelector("button[type=submit]").disabled);
    equal(fixture.sdk.widgets.size, 1);
    fixture.sdk.solve("mod-form");
    assert(!doc.getElementById("mod-form").querySelector("button[type=submit]").disabled);
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
