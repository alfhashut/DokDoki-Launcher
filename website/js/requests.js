"use strict";

(() => {
  // Public production configuration. Private credentials belong only in the Worker.
  const SUBMISSIONS_ENABLED = true;
  const API_BASE_URL = "https://dokdoki-requests.fanundertale86.workers.dev";
  const TURNSTILE_SITE_KEY = "0x4AAAAAAFf0QrSXza1H1hXL";
  let apiURL = null;
  if (API_BASE_URL) {
    try {
      const url = new URL(API_BASE_URL);
      const localHTTP = url.protocol === "http:" && ["localhost", "127.0.0.1", "[::1]"].includes(url.hostname);
      if ((url.protocol === "https:" || localHTTP) && !url.username && !url.password && !url.search && !url.hash) {
        apiURL = `${url.href.replace(/\/+$/, "")}/api/report`;
      }
    } catch { /* Leave submissions disabled for invalid configuration. */ }
  }
  const forms = Array.from(document.querySelectorAll(".request-form"));
  const picker = document.getElementById("request-type");
  const completed = new Set();
  const fieldErrors = new Map();
  const verification = new Map();
  const configured = SUBMISSIONS_ENABLED && Boolean(apiURL && TURNSTILE_SITE_KEY);
  let turnstileReady = false;
  let scriptLoading = null;
  let scriptFailed = false;
  const definitions = {
    "bug-form": { type: "bug", action: "report_bug", fields: { dokdokiVersion: "bug-version", operatingSystem: "bug-os", whatHappened: "bug-happened", stepsToReproduce: "bug-steps", additionalInformation: "bug-additional" } },
    "mod-form": { type: "mod-request", action: "request_mod", fields: { modName: "mod-name", sourceUrl: "mod-url", modAuthor: "mod-author", modVersion: "mod-version", additionalInformation: "mod-additional" } },
    "removal-form": { type: "removal-request", action: "request_removal", fields: { modName: "removal-name", sourceUrl: "removal-url", reason: "removal-reason", rightsHolder: "removal-rights", proofUrl: "removal-proof", additionalInformation: "removal-additional" } }
  };
  let sending = false;

  if (configured) document.getElementById("submission-notice").textContent = "Complete the verification before submitting. Your report will be publicly visible on GitHub and this website.";

  function clearToken(state) {
    state.token = "";
    state.issuedAt = 0;
  }

  function removeWidget(form) {
    const state = verification.get(form);
    clearToken(state);
    state.generation += 1;
    const id = state.widgetId;
    state.widgetId = null;
    if (id !== null) {
      try { window.turnstile.remove(id); } catch { /* Stay locked if cleanup fails. */ }
    }
    state.container.replaceChildren();
  }

  function verificationFailed(form) {
    const state = verification.get(form);
    clearToken(state);
    state.message.textContent = "Verification failed — please try again.";
    state.retry.hidden = false;
    updateButtons();
  }

  function resetVerification(form, message = "Checking…") {
    const state = verification.get(form);
    clearToken(state);
    state.message.textContent = message;
    state.retry.hidden = true;
    updateButtons();
    try {
      if (state.widgetId !== null) window.turnstile.reset(state.widgetId);
      else renderVerification(form);
    } catch { verificationFailed(form); }
  }

  function renderVerification(form) {
    const state = verification.get(form);
    if (!configured || !turnstileReady || form.hidden || completed.has(form) || state.widgetId !== null) return;
    const generation = ++state.generation;
    const current = () => state.generation === generation && !form.hidden && !completed.has(form) && !sending;
    state.message.textContent = "Checking…";
    state.retry.hidden = true;
    try {
      state.widgetId = window.turnstile.render(state.container, {
        sitekey: TURNSTILE_SITE_KEY,
        action: definitions[form.id].action,
        theme: "auto",
        size: "compact",
        // Keep tokens out of hidden form fields and handle refresh explicitly.
        "response-field": false,
        "refresh-expired": "manual",
        "refresh-timeout": "manual",
        retry: "never",
        callback: (token) => {
          if (!current()) return;
          if (typeof token !== "string" || !token || token.length > 2048 || /\s/u.test(token)) {
            verificationFailed(form);
            return;
          }
          state.token = token;
          state.issuedAt = Date.now();
          state.message.textContent = "Verification complete.";
          state.retry.hidden = true;
          updateButtons();
        },
        "expired-callback": () => {
          if (current()) resetVerification(form, "Verification expired — please try again.");
        },
        "timeout-callback": () => {
          if (current()) resetVerification(form, "Verification expired — please try again.");
        },
        "error-callback": (errorCode) => {
          if (current()) {
            // Cloudflare client codes are six digits; reject arbitrary payloads.
            if (typeof errorCode === "string" && errorCode.length === 6 && /^\d{6}$/.test(errorCode)) {
              console.warn("Turnstile client error:", errorCode);
            }
            verificationFailed(form);
          }
          return true;
        },
        "unsupported-callback": () => { if (current()) verificationFailed(form); }
      });
    } catch { verificationFailed(form); }
  }

  function loadTurnstile() {
    if (!configured) return null;
    // Also recover a late API load after a timeout, without inserting a script.
    if (window.turnstile && (!scriptLoading || scriptFailed)) {
      turnstileReady = true;
      scriptFailed = false;
      scriptLoading = Promise.resolve(window.turnstile);
    }
    if (scriptLoading) return scriptLoading;
    scriptLoading = new Promise((resolve, reject) => {
      const src = "https://challenges.cloudflare.com/turnstile/v0/api.js?render=explicit";
      const existing = Array.from(document.scripts).find((script) => script.src === src);
      const script = existing || document.createElement("script");
      if (!existing) {
        script.id = "dokdoki-turnstile-api";
        script.src = src;
        script.async = true;
      }
      let settled = false;
      const cleanup = () => {
        clearTimeout(timeout);
        script.removeEventListener("error", fail);
        script.removeEventListener("load", loaded);
      };
      const fail = () => {
        if (settled) return;
        settled = true;
        scriptFailed = true;
        cleanup();
        reject(new Error("Verification unavailable"));
      };
      const loaded = () => {
        if (settled) return;
        if (!window.turnstile) { fail(); return; }
        settled = true;
        cleanup();
        turnstileReady = true;
        resolve(window.turnstile);
      };
      const timeout = setTimeout(fail, 15000);
      script.addEventListener("error", fail);
      script.addEventListener("load", loaded);
      if (!existing) document.head.append(script);
    });
    return scriptLoading;
  }

  function prepareVerification() {
    const loading = loadTurnstile();
    if (!loading) return;
    loading.then(() => {
      const active = forms.find((form) => !form.hidden);
      if (active) renderVerification(active);
    }).catch(() => {
      for (const form of forms) {
        if (completed.has(form)) continue;
        verificationFailed(form);
        verification.get(form).message.textContent = "Verification failed — please try again. Reload this page if verification cannot load.";
      }
    });
  }

  function validate(field, error) {
    field.setCustomValidity("");
    const value = field.value.trim();
    if (field.required && !value) {
      field.setCustomValidity("Please fill out this field.");
    } else if (value && field.type === "url") {
      try {
        const url = new URL(value);
        if (!["http:", "https:"].includes(url.protocol) || !url.hostname || url.username || url.password || /\s/u.test(value)) {
          throw new Error("Invalid URL");
        }
      } catch {
        field.setCustomValidity("Enter a full http:// or https:// URL without spaces or login details.");
      }
    } else if (value && field.minLength > 0 && value.length < field.minLength) {
      field.setCustomValidity(`Please enter at least ${field.minLength} characters.`);
    }
    if (field.maxLength > 0 && value.length > field.maxLength) {
      field.setCustomValidity(`Please use no more than ${field.maxLength} characters.`);
    }
    const invalid = !field.validity.valid;
    field.setAttribute("aria-invalid", String(invalid));
    error.textContent = invalid ? field.validationMessage : "";
  }

  function updateButtons() {
    picker.disabled = sending;
    for (const form of forms) {
      const button = form.querySelector("button[type=submit]");
      button.disabled = !configured || !verification.get(form)?.token || sending || completed.has(form);
      button.textContent = completed.has(form) ? "Report submitted" : sending && form.getAttribute("aria-busy") === "true" ? "Submitting…" : configured ? "Submit request" : "Submit request (unavailable)";
      const retry = verification.get(form)?.retry;
      if (retry) retry.disabled = sending;
    }
  }

  async function submit(form, result) {
    if (!configured || sending || completed.has(form)) return;
    const definition = definitions[form.id];
    for (const field of form.querySelectorAll("input, textarea, select")) validate(field, fieldErrors.get(field));
    if (!form.reportValidity()) return;
    const state = verification.get(form);
    if (!state.token) {
      state.message.textContent = "Checking… Complete verification before submitting.";
      return;
    }
    if (Date.now() - state.issuedAt >= 300000) {
      resetVerification(form, "Verification expired — please try again.");
      return;
    }
    // Lock synchronously, before the first await, including all other forms.
    sending = true;
    form.setAttribute("aria-busy", "true");
    form.querySelector("fieldset").disabled = true;
    updateButtons();
    result.className = "notice";
    result.textContent = "Submitting…";
    const payload = { type: definition.type };
    for (const [key, id] of Object.entries(definition.fields)) payload[key] = document.getElementById(id).value.trim();
    payload.turnstileToken = state.token;
    clearToken(state);
    const body = JSON.stringify(payload);
    delete payload.turnstileToken;
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), 25000);
    try {
      const response = await fetch(apiURL, {
        method: "POST",
        headers: { "Content-Type": "application/json", Accept: "application/json" },
        credentials: "omit",
        referrerPolicy: "no-referrer",
        redirect: "error",
        signal: controller.signal,
        body
      });
      const data = await response.json();
      if (response.ok && data?.ok === true && Number.isSafeInteger(data.requestNumber) && data.requestNumber > 0 && data.status === "received") {
        completed.add(form);
        removeWidget(form);
        state.wrapper.hidden = true;
        const heading = document.createElement("p");
        heading.textContent = "✓ Report submitted";
        const number = document.createElement("p");
        number.textContent = `Request #${data.requestNumber}`;
        const status = document.createElement("p");
        status.textContent = "Status: Received";
        const link = document.createElement("a");
        link.href = `status.html?q=${data.requestNumber}`;
        link.textContent = "View status";
        result.replaceChildren(heading, number, status, link);
        result.focus();
      } else if (!response.ok && data?.error === "validation_error") {
        result.className = "notice error";
        const message = typeof data.message === "string" ? data.message.slice(0, 300) : "Please check the required fields.";
        result.textContent = message;
        const id = Object.hasOwn(definition.fields, data.field) ? definition.fields[data.field] : null;
        if (id) {
          const field = document.getElementById(id);
          field.setCustomValidity(message);
          field.setAttribute("aria-invalid", "true");
          fieldErrors.get(field).textContent = message;
        }
      } else if (data?.error === "verification_failed") {
        result.className = "notice error";
        result.textContent = "Verification failed — please try again.";
      } else {
        result.className = "notice error";
        result.textContent = data?.error === "submissions_disabled" ? "Online submissions are not enabled yet." : "Submission failed — please try again. Check request status first if the result is uncertain.";
      }
    } catch {
      // A lost response can follow a successful write. Never retry automatically.
      result.className = "notice error";
      result.textContent = "The submission could not be confirmed. Check request status before trying again. ";
      const link = document.createElement("a");
      link.href = "status.html";
      link.textContent = "Check request status";
      result.append(link);
    } finally {
      clearTimeout(timeout);
      sending = false;
      form.removeAttribute("aria-busy");
      form.querySelector("fieldset").disabled = completed.has(form);
      if (!completed.has(form)) resetVerification(form);
      updateButtons();
    }
  }

  for (const form of forms) {
    const result = document.createElement("div");
    result.id = `${form.id}-result`;
    result.setAttribute("role", "status");
    result.setAttribute("aria-live", "polite");
    result.tabIndex = -1;
    form.append(result);
    const wrapper = document.createElement("div");
    wrapper.className = "verification";
    wrapper.hidden = !configured;
    const container = document.createElement("div");
    container.id = `${form.id}-turnstile`;
    const message = document.createElement("p");
    message.className = "help verification-message";
    message.setAttribute("role", "status");
    message.textContent = "Checking…";
    const retry = document.createElement("button");
    retry.type = "button";
    retry.className = "button";
    retry.textContent = "Retry verification";
    retry.hidden = true;
    retry.addEventListener("click", () => {
      if (sending || completed.has(form)) return;
      if (!turnstileReady) {
        message.textContent = "Checking…";
        retry.hidden = true;
        prepareVerification();
      } else resetVerification(form);
    });
    wrapper.append(container, message, retry);
    form.insertBefore(wrapper, form.querySelector("button[type=submit]"));
    verification.set(form, { wrapper, container, message, retry, token: "", issuedAt: 0, widgetId: null, generation: 0 });
    form.addEventListener("submit", (event) => {
      event.preventDefault();
      submit(form, result);
    });
    for (const field of form.querySelectorAll("input, textarea, select")) {
      const error = document.createElement("span");
      error.id = `${field.id}-error`;
      error.className = "field-error";
      error.setAttribute("aria-live", "polite");
      field.parentElement.append(error);
      fieldErrors.set(field, error);
      const description = field.getAttribute("aria-describedby");
      field.setAttribute("aria-describedby", [description, error.id].filter(Boolean).join(" "));
      field.addEventListener("blur", () => validate(field, error));
      field.addEventListener("input", () => {
        if (field.hasAttribute("aria-invalid")) validate(field, error);
      });
      field.addEventListener("change", () => validate(field, error));
    }
    if (configured) document.getElementById(form.querySelector("button[type=submit]").getAttribute("aria-describedby")).textContent = "Complete verification and submit only once. Your request number will appear here.";
    // Fields stay disabled without JavaScript. Configuration controls submission.
    form.querySelector("fieldset").disabled = false;
  }

  function selectForm() {
    if (sending) return;
    for (const form of forms) {
      form.hidden = form.id !== picker.value;
      if (form.hidden) removeWidget(form);
    }
    const active = forms.find((form) => !form.hidden);
    if (active) renderVerification(active);
    updateButtons();
  }

  picker.addEventListener("change", selectForm);
  selectForm();
  document.getElementById("request-picker").hidden = false;
  updateButtons();
  if (configured) prepareVerification();
  window.addEventListener("pagehide", () => {
    for (const form of forms) removeWidget(form);
    updateButtons();
  });
  window.addEventListener("pageshow", (event) => {
    if (event.persisted && configured) selectForm();
  });
})();
