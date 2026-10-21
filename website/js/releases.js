"use strict";

(() => {
  const endpoint = "https://api.github.com/repos/alfhashut/DokDoki-Launcher/releases";
  const downloadPrefix = "/alfhashut/DokDoki-Launcher/releases/download/";
  const root = document.querySelector("[data-releases]");
  if (!root) return;
  const isHome = root.dataset.releases === "home";
  const message = document.getElementById("release-message");
  const retry = document.getElementById("release-retry");
  const selector = document.getElementById("release-select");
  let releases = [];

  function downloadURL(value) {
    try {
      const url = new URL(value);
      if (url.origin === "https://github.com" && !url.username && !url.password && url.pathname.startsWith(downloadPrefix)) {
        return url.href;
      }
    } catch { /* Missing or unsafe asset URL. */ }
    return null;
  }

  function releaseName(release) {
    return release.tag_name || release.name || "Untitled release";
  }

  function assetFor(release, name) {
    const assets = Array.isArray(release.assets) ? release.assets : [];
    return assets.find((asset) => asset && asset.name === name && downloadURL(asset.browser_download_url));
  }

  function downloadControl(release, filename, label, primary = false) {
    const asset = assetFor(release, filename);
    const control = document.createElement(asset ? "a" : "button");
    control.className = primary ? "button primary" : "button";
    control.textContent = asset ? label : `${label} (unavailable)`;
    if (asset) {
      // Always use the API-provided asset URL, never a constructed version URL.
      control.href = downloadURL(asset.browser_download_url);
    } else {
      control.type = "button";
      control.disabled = true;
    }
    return control;
  }

  function showRelease(release) {
    document.getElementById("release-version").textContent = releaseName(release);
    if (isHome) {
      document.getElementById("release-version").hidden = false;
      document.getElementById("home-downloads").replaceChildren(
        downloadControl(release, "DokDoki-Launcher.exe", "Download for Windows", true),
        downloadControl(release, "DokDoki-Launcher-Linux-x86_64.AppImage", "Download for Linux")
      );
      return;
    }

    const date = new Date(release.published_at);
    document.getElementById("release-date").textContent = Number.isNaN(date.getTime()) ? "Publication date unavailable" : `Published ${date.toLocaleDateString(undefined, { year: "numeric", month: "short", day: "numeric" })}`;
    document.getElementById("prerelease-notice").hidden = !release.prerelease;
    const assets = document.getElementById("release-assets");
    assets.replaceChildren();
    for (const [platform, architecture, filename] of [
      ["Windows", "x64 · EXE", "DokDoki-Launcher.exe"],
      ["Linux", "x86_64 · AppImage", "DokDoki-Launcher-Linux-x86_64.AppImage"]
    ]) {
      const box = document.createElement("div");
      box.className = "download-option";
      const title = document.createElement("h3");
      title.textContent = platform;
      const detail = document.createElement("p");
      detail.className = "metadata";
      detail.textContent = architecture;
      const name = document.createElement("p");
      name.className = "filename muted";
      name.textContent = filename;
      box.append(title, detail, name, downloadControl(release, filename, `Download for ${platform}`, platform === "Windows"));
      assets.append(box);
    }
    const checksums = document.getElementById("release-checksums");
    const checksum = assetFor(release, "SHA256SUMS.txt");
    const checksumControl = document.createElement(checksum ? "a" : "p");
    checksumControl.textContent = checksum ? "Download SHA256SUMS.txt" : "Checksums are not available for this release.";
    if (checksum) checksumControl.href = downloadURL(checksum.browser_download_url);
    else checksumControl.className = "help";
    checksums.replaceChildren(checksumControl);
    document.getElementById("release-details").hidden = false;
  }

  async function loadReleases() {
    retry.hidden = true;
    message.className = "muted";
    message.textContent = "Loading published releases…";
    if (selector) selector.disabled = true;
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), 20000);
    try {
      const published = [];
      let page = 1;
      let hasNext;
      do {
        const response = await fetch(`${endpoint}?per_page=100&page=${page}`, {
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
        if (!Array.isArray(data)) throw new Error("GitHub returned an unexpected release response.");
        published.push(...data.filter((release) => release && !release.draft && release.published_at && typeof release.tag_name === "string"));
        hasNext = /<[^>]+>;\s*rel="next"/.test(response.headers.get("link") || "");
        page += 1;
      } while (hasNext);

      const byNewest = (a, b) => (Date.parse(b.published_at) || 0) - (Date.parse(a.published_at) || 0);
      releases = published.sort(byNewest);
      const stable = releases.find((release) => !release.prerelease);
      // Keep the latest stable first; the remaining releases stay newest-first.
      if (stable) releases = [stable, ...releases.filter((release) => release !== stable)];
      if (!releases.length) {
        message.textContent = "No published releases are available yet.";
        if (selector) selector.replaceChildren(new Option("No published releases", ""));
        return;
      }
      if (isHome) {
        if (stable) {
          message.textContent = "Latest stable version";
          showRelease(stable);
        } else {
          message.textContent = "No stable release is available yet. Prereleases can be found under View all versions.";
        }
      } else {
        selector.replaceChildren();
        releases.forEach((release, index) => {
          const suffix = release === stable ? " · Latest stable" : release.prerelease ? " · Prerelease" : "";
          selector.append(new Option(`${releaseName(release)}${suffix}`, String(index)));
        });
        selector.disabled = false;
        message.textContent = stable ? "Choose a release to see its available downloads." : "No stable release is available yet. Only prereleases are listed.";
        showRelease(releases[0]);
      }
    } catch (error) {
      message.className = "notice error";
      const detail = error.name === "AbortError" ? "The request timed out." : error instanceof TypeError ? "Check your connection or try again later." : error.message;
      message.textContent = `Could not load releases. ${detail}`;
      if (selector) selector.replaceChildren(new Option("Releases unavailable", ""));
      retry.hidden = false;
    } finally {
      clearTimeout(timeout);
    }
  }

  if (selector) selector.addEventListener("change", () => {
    const release = releases[Number(selector.value)];
    if (release) showRelease(release);
  });
  retry.addEventListener("click", loadReleases);
  loadReleases();
})();
