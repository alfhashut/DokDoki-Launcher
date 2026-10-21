"use strict";

(() => {
  const endpoint = "https://api.github.com/repos/alfhashut/DokDoki-Launcher/issues";
  // These mappings are the category/status contract for the future submission service.
  const categories = new Map([
    ["bug", "Bug report"],
    ["mod-request", "Mod request"],
    ["removal-request", "Removal request"]
  ]);
  // If labels conflict, display the furthest decision; never infer completion from closure.
  const statuses = new Map([
    ["status:declined", "Declined"],
    ["status:completed", "Completed"],
    ["status:fixed", "Fixed"],
    ["status:accepted", "Accepted"],
    ["status:reviewing", "Under review"],
    ["status:received", "Received"]
  ]);
  const search = document.getElementById("request-search");
  const message = document.getElementById("status-message");
  const count = document.getElementById("status-count");
  const list = document.getElementById("issue-list");
  const loadMore = document.getElementById("status-load-more");
  const retry = document.getElementById("status-retry");
  const issues = new Map();
  const initialQuery = (new URLSearchParams(window.location.search).get("q") || "").slice(0, 200).trim();
  search.value = initialQuery;
  const initialNumber = /^#?\d+$/.test(initialQuery) ? Number(initialQuery.replace(/^#/, "")) : null;
  let nextPage = 1;
  let hasMore = true;
  let loading = false;

  function sourceURL(value, number) {
    try {
      const url = new URL(value);
      if (url.origin === "https://github.com" && !url.username && !url.password && url.pathname === `/alfhashut/DokDoki-Launcher/issues/${number}`) return url.href;
    } catch { /* Do not render an invalid source URL. */ }
    return null;
  }

  function labelsFor(issue) {
    if (!Array.isArray(issue.labels)) return [];
    return issue.labels.map((label) => typeof label === "string" ? label : label && label.name)
      .filter((label) => typeof label === "string").map((label) => label.toLowerCase());
  }

  function detail(label, value) {
    const group = document.createElement("div");
    const term = document.createElement("dt");
    term.textContent = `${label}:`;
    const definition = document.createElement("dd");
    if (typeof value === "string") definition.textContent = value;
    else definition.append(value);
    group.append(term, definition);
    return group;
  }

  function issueItem(issue) {
    const item = document.createElement("li");
    const heading = document.createElement("h2");
    const number = document.createElement("span");
    number.className = "issue-number";
    number.textContent = `#${issue.number} `;
    heading.append(number, document.createTextNode(issue.title));
    const labels = labelsFor(issue);
    const category = [...categories].filter(([label]) => labels.includes(label)).map(([, name]) => name).join(", ") || "Uncategorized";
    const status = [...statuses].find(([label]) => labels.includes(label));
    const state = issue.state === "closed" ? "Closed" : "Open";
    const badge = document.createElement("span");
    badge.className = "badge";
    badge.textContent = status ? status[1] : `${state} (no status label)`;
    const updated = new Date(issue.updated_at || "");
    const date = document.createElement("time");
    if (Number.isNaN(updated.getTime())) {
      date.textContent = "Unknown";
    } else {
      date.dateTime = updated.toISOString();
      date.textContent = updated.toLocaleDateString(undefined, { year: "numeric", month: "short", day: "numeric" });
    }
    const details = document.createElement("dl");
    details.className = "issue-details";
    details.append(detail("Category", category), detail("Status", badge), detail("State", state), detail("Updated", date));
    item.append(heading, details);
    const url = sourceURL(issue.html_url, issue.number);
    if (url) {
      const source = document.createElement("a");
      source.className = "source-link";
      source.href = url;
      source.textContent = "View source on GitHub";
      source.setAttribute("aria-label", `View source for request #${issue.number} on GitHub`);
      item.append(source);
    }
    return item;
  }

  function render() {
    const query = search.value.trim().toLowerCase();
    const numberQuery = /^#?\d+$/.test(query) ? Number(query.replace(/^#/, "")) : null;
    const matches = [...issues.values()].filter((issue) => numberQuery !== null
      ? issue.number === numberQuery : issue.title.toLowerCase().includes(query));
    matches.sort((a, b) => (Date.parse(b.updated_at) || 0) - (Date.parse(a.updated_at) || 0));
    list.replaceChildren(...matches.map(issueItem));
    count.textContent = `${matches.length} matching request${matches.length === 1 ? "" : "s"} of ${issues.size} loaded.`;
    if (!matches.length) {
      const empty = document.createElement("li");
      empty.className = "muted";
      empty.textContent = initialNumber !== null && numberQuery === initialNumber
        ? `Request #${initialNumber} is not visible in the loaded public data yet. A new request may take a moment to appear. Refresh this page shortly${hasMore ? ", or load older requests below" : ""}.`
        : query ? "No loaded requests match your search. Try another title or number, or load older requests if available." : "No requests to display yet.";
      list.append(empty);
    }
  }

  async function loadPage() {
    if (loading || !hasMore) return;
    loading = true;
    loadMore.disabled = true;
    retry.hidden = true;
    message.className = "muted";
    message.textContent = nextPage === 1 ? "Loading request status…" : "Loading older requests…";
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), 15000);
    try {
      const response = await fetch(`${endpoint}?state=all&sort=updated&direction=desc&per_page=100&page=${nextPage}`, {
        headers: { Accept: "application/vnd.github+json" },
        credentials: "omit",
        referrerPolicy: "no-referrer",
        signal: controller.signal
      });
      if (!response.ok) {
        if ([403, 429].includes(response.status)) throw new Error("GitHub is limiting requests. Please try again later.");
        throw new Error(`GitHub returned HTTP ${response.status}. Please try again later.`);
      }
      const data = await response.json();
      if (!Array.isArray(data)) throw new Error("GitHub returned an unexpected request response.");
      for (const issue of data) {
        // The Issues endpoint includes pull requests; exclude them even if labelled.
        if (!issue || Object.hasOwn(issue, "pull_request") || !Number.isSafeInteger(issue.number) || issue.number < 1 || typeof issue.title !== "string") continue;
        issues.set(issue.number, issue);
      }
      hasMore = /<[^>]+>;\s*rel="next"/.test(response.headers.get("link") || "");
      nextPage += 1;
      search.disabled = false;
      loadMore.hidden = !hasMore;
      message.textContent = hasMore ? "Loaded recent requests. Older requests are available below." : "All available requests have been loaded.";
      render();
    } catch (error) {
      message.className = "notice error";
      const detail = error.name === "AbortError" ? "The request timed out." : error instanceof TypeError ? "Check your connection or try again later." : error.message;
      message.textContent = `Could not load ${nextPage === 1 ? "request status" : "older requests"}. ${detail}${issues.size ? " Previously loaded requests are still searchable." : ""}`;
      loadMore.hidden = true;
      retry.hidden = false;
    } finally {
      clearTimeout(timeout);
      loading = false;
      loadMore.disabled = false;
    }
  }

  search.addEventListener("input", render);
  loadMore.addEventListener("click", loadPage);
  retry.addEventListener("click", loadPage);
  loadPage();
})();
