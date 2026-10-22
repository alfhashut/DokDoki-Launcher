import { assert, equal, test } from "./helpers.js";

test("Production: Wrangler enables the exact workers.dev deployment and preserves strict origins", async () => {
  const response = await fetch("/worker/wrangler.jsonc");
  assert(response.ok);
  const config = JSON.parse(await response.text());
  equal(config.name, "dokdoki-requests");
  equal(config.main, "src/index.js");
  equal(config.compatibility_date, "2026-10-21");
  equal(config.workers_dev, true);
  equal(config.preview_urls, false);
  equal(config.keep_vars, true);
  equal(config.vars, { SUBMISSIONS_ENABLED: "true", ALLOWED_ORIGINS: "https://alfhashut.github.io" });
  equal(config.secrets, { required: ["GITHUB_TOKEN", "TURNSTILE_SECRET_KEY"] });
  assert(!Object.hasOwn(config, "route") && !Object.hasOwn(config, "routes"));
  equal(config.observability, {
    enabled: true,
    logs: { enabled: true, invocation_logs: true }
  });
});

test("Production: required secret names match the Worker's exact env bindings", async () => {
  const [configResponse, sourceResponse] = await Promise.all([
    fetch("/worker/wrangler.jsonc"), fetch("/worker/src/index.js")
  ]);
  assert(configResponse.ok && sourceResponse.ok);
  const config = JSON.parse(await configResponse.text());
  const source = await sourceResponse.text();
  const accessed = new Set();
  for (const match of source.matchAll(/\b(?:GITHUB_TOKEN|TURNSTILE_SECRET_KEY)\b/g)) {
    equal(source.slice(match.index - 4, match.index), "env.", "Secrets must come only from Worker env bindings");
    accessed.add(match[0]);
  }
  equal([...accessed].sort(), [...config.secrets.required].sort());
  assert(source.includes("verifyTurnstile(input.turnstileToken, input.type, env.TURNSTILE_SECRET_KEY)"));
  assert(source.includes('github("/issues", env.GITHUB_TOKEN, "POST",'));
  assert(source.includes("applyLabels(number, report.labels, env.GITHUB_TOKEN)"));
});

test("Production: Worker source and configuration contain no embedded credential values", async () => {
  const [configResponse, sourceResponse] = await Promise.all([
    fetch("/worker/wrangler.jsonc"), fetch("/worker/src/index.js")
  ]);
  assert(configResponse.ok && sourceResponse.ok);
  const config = JSON.parse(await configResponse.text());
  const source = await sourceResponse.text();
  equal(config.secrets, { required: ["GITHUB_TOKEN", "TURNSTILE_SECRET_KEY"] });
  for (const name of config.secrets.required) assert(!Object.hasOwn(config.vars, name), "Secret values must not be Wrangler vars");
  const combined = source + JSON.stringify(config);
  const credentialPatterns = [
    /\bgh[pousr]_[A-Za-z0-9]{20,255}\b/,
    /\bgithub_pat_[A-Za-z0-9_]{30,255}\b/,
    /\b0x[A-Za-z0-9_-]{20,}\b/,
    /["'`]Bearer\s+[A-Za-z0-9._~+/=-]{16,}/,
    /\b(?:GITHUB_TOKEN|TURNSTILE_SECRET_KEY)\b["']?\s*[:=]\s*["'`][^"'`\r\n]+["'`]/,
    /-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----/
  ];
  assert(credentialPatterns.every((pattern) => !pattern.test(combined)), "Embedded credential pattern found (value withheld)");
});

test("Production: every page retains the disclaimer and project-subpath navigation/assets", async () => {
  const disclaimer = "DokDoki Launcher is an unofficial, fan-made project. It is not affiliated with, endorsed by, or associated with Team Salvato.";
  const base = new URL("/DokDoki-Launcher/", location.origin);
  for (const name of ["index.html", "downloads.html", "requests.html", "status.html"]) {
    const response = await fetch(new URL(name, base));
    assert(response.ok, name);
    const doc = new DOMParser().parseFromString(await response.text(), "text/html");
    assert(doc.querySelector("footer").textContent.includes(disclaimer), name + " disclaimer");
    if (name === "requests.html") {
      assert(doc.body.textContent.includes("Submitted reports and requests are stored publicly as GitHub Issues. Do not include private or sensitive information."));
    }
    for (const node of doc.querySelectorAll("[src], link[href], nav a[href]")) {
      const ref = node.getAttribute("src") ?? node.getAttribute("href");
      const url = new URL(ref, base);
      if (url.origin !== location.origin) continue;
      assert(!ref.startsWith("/"), name + " requires relative references: " + ref);
      assert(url.pathname.startsWith(base.pathname));
      assert((await fetch(url)).ok, name + " missing asset/navigation: " + ref);
    }
  }
});
