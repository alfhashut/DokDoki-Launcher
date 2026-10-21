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
  equal(config.vars, { SUBMISSIONS_ENABLED: "true", ALLOWED_ORIGINS: "https://alfhashut.github.io" });
  assert(!Object.hasOwn(config, "route") && !Object.hasOwn(config, "routes"));
  equal(config.observability.enabled, false);
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
