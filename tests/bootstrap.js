"use strict";

// Injected by the loopback test server only for pages with a test case ID.
// This file is outside website/ and is never part of the Pages artifact.
(() => {
  const key = new URLSearchParams(location.search).get("_test");
  const fixture = parent.DOKDOKI_TEST_CASES.get(key);
  fixture.calls = [];
  fixture.scriptLoads = [];
  fixture.consoleWarnings = [];
  console.warn = (...args) => fixture.consoleWarnings.push(args);
  const widgets = new Map();
  const sdk = {
    widgets, renders: [], resets: [], removed: [], readyCalls: 0, autoSolve: fixture.autoSolve !== false,
    ready() { sdk.readyCalls += 1; throw new Error("Explicit rendering must not use ready()"); },
    render(container, options) {
      if ([...widgets.values()].some((widget) => widget.container === container)) throw new Error("Duplicate active widget");
      if (fixture.renderError) throw new Error("Simulated widget render failure");
      const id = String(sdk.renders.length);
      const widget = { id, form: container.closest("form").id, container, options };
      widgets.set(id, widget);
      sdk.renders.push(widget);
      const control = document.createElement("button");
      control.type = "button";
      control.className = "button";
      control.textContent = "Verification (test fixture)";
      control.addEventListener("click", () => sdk.solve(widget.form));
      container.append(control);
      if (sdk.autoSolve) options.callback("XXXX.DUMMY.TOKEN.XXXX");
      return id;
    },
    solve(form, token = "XXXX.DUMMY.TOKEN.XXXX") {
      [...widgets.values()].find((widget) => widget.form === form)?.options.callback(token);
    },
    reset(id) {
      sdk.resets.push(id);
      if (sdk.autoSolve) widgets.get(id)?.options.callback("XXXX.DUMMY.TOKEN.XXXX");
    },
    remove(id) {
      const widget = widgets.get(id);
      widget.container.replaceChildren();
      sdk.removed.push(widget);
      widgets.delete(id);
    }
  };
  fixture.sdk = sdk;
  const append = document.head.append.bind(document.head);
  const src = "https://challenges.cloudflare.com/turnstile/v0/api.js?render=explicit";
  const load = (node) => {
    const complete = () => {
      if (fixture.scriptError) node.dispatchEvent(new Event("error"));
      else {
        if (!fixture.missingAPI) window.turnstile = sdk;
        node.dispatchEvent(new Event("load"));
      }
    };
    if (fixture.holdScript) fixture.releaseScript = complete;
    else queueMicrotask(complete);
  };
  document.head.append = (...nodes) => {
    for (const node of nodes) {
      if (node.tagName === "SCRIPT" && node.src === src) {
        fixture.scriptLoads.push(node.src);
        // Keep a real DOM element for singleton checks without fetching the SDK.
        node.type = "application/x-dokdoki-test";
        append(node);
        load(node);
      } else append(node);
    }
  };
  if (fixture.existingAPI) window.turnstile = sdk;
  if (fixture.existingScript) {
    const script = document.createElement("script");
    script.type = "application/x-dokdoki-test";
    script.src = src;
    if (fixture.existingScript === "id") script.id = "dokdoki-turnstile-api";
    append(script);
    fixture.existingScriptElement = script;
    load(script);
  }
  if (fixture.scriptTimeout) {
    const schedule = window.setTimeout.bind(window);
    window.setTimeout = (callback, delay) => schedule(callback, delay === 15000 ? 10 : delay);
  }
  window.fetch = async (url, options = {}) => {
    fixture.calls.push({ url: String(url), ...options });
    const next = fixture.responses.shift();
    if (!next) throw new Error("Unexpected fetch in browser test");
    if (next.handler) return next.handler(url, options);
    if (next.hold) await new Promise((resolve) => { fixture.release = resolve; });
    if (next.offline) throw new TypeError("Simulated network failure");
    if (next.timeout) throw new DOMException("Aborted", "AbortError");
    return new Response(next.raw ?? JSON.stringify(next.data), {
      status: next.status || 200,
      headers: next.next ? { Link: '<https://api.github.com/unused>; rel="next"' } : {}
    });
  };
})();
