# Website and Worker checks

The tests use browser-native JavaScript modules, Request/Response, streams and
AbortController. Turnstile SDK behavior, Siteverify, GitHub, and website submissions
are mocked, using only documented Cloudflare test keys/token. No real
token, account, backend, or external network is required. Tests live outside
`website/`, so the existing Pages workflow does not publish them.

To run in any modern browser, from the repository root:

```sh
python3 tests/serve.py
```

Open `http://127.0.0.1:8000/tests/`. Results appear on the page. Stop the server
with Ctrl+C. The server binds only to loopback and serves only website assets,
test HTML/JS, and the Worker source. It cannot serve `.dev.vars`, `.env`, Git data,
or the launcher. For test iframes only, it injects the fixture bootstrap before
the production script; application files are not rewritten on disk.

The project already uses PySide6. To automate the same tests in its environment:

```sh
.venv/bin/python tests/run_browser_checks.py
```

This uses the installed Qt WebEngine in offscreen mode and exits nonzero on a
failure. It additionally blocks external HTTP(S) requests. No browser, Node/npm
package, or framework needs to be installed for the tests in this environment.
If the execution sandbox blocks browser subprocesses, run the check with the
host's normal browser permissions; do not disable the browser sandbox.

Coverage includes all three issue formats, server validation and size limits,
CORS/preflights, disabled gates, authentication/header handling, safe failures,
missing labels, label errors, no issue-create retries, no credential leaks,
literal Markdown content, client validation, duplicate-click protection,
success/request number/internal status links, status query initialization and
eventual-visibility messages, and Phase 1 releases/status regressions. Turnstile
coverage includes exact hostname and per-type actions, unusable/expired/replayed
tokens, network/HTTP/JSON failures, no GitHub calls on failed verification, no
token/secret leaks, all configuration gates, missing client verification, token
reset/clear on attempts and success, widget/script error recovery, expiry,
form switching, and back/forward-cache restoration.

The bootstrap intercepts the dynamically loaded official Turnstile script URL and
supplies a minimal SDK stand-in. It does not download a live widget or call real
Siteverify. The production source has no mock hooks or verification bypass. The
small mock control is labelled "test fixture"; it is not a claim about Cloudflare's
actual widget appearance. Production hostname/action checks stay exact even in
private development; see worker/README.md for the limits of real test-key replies.

These are real-browser checks of the Worker fetch module and web-standard APIs,
not a claim that Wrangler/workerd or a deployed Cloudflare environment was run.
Optional local Wrangler health checks are described in [worker/README.md](../worker/README.md).
