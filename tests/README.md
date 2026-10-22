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
test HTML/JS, the Worker source, and its public Wrangler configuration. It cannot serve `.dev.vars`, `.env`, Git data,
or the launcher. For test iframes only, it injects the fixture bootstrap before
the production script. Configuration variants are served only when the test
helper supplies an explicit `_test`/`_config` fixture; application files are not
rewritten on disk. The deployed website has no query configuration overrides.
The default fixture exercises the checked-in production URL, public site key,
and enabled gate while still mocking all upstream requests.

The project already uses PySide6. To automate the same tests in its environment:

```sh
.venv/bin/python tests/run_browser_checks.py
```

This uses the installed Qt WebEngine in offscreen mode and exits nonzero on a
failure. It additionally blocks external HTTP(S) requests. No browser, Node/npm
package, or framework needs to be installed for the tests in this environment.
If the execution sandbox blocks browser subprocesses, run the check with the
host's normal browser permissions; do not disable the browser sandbox.

Coverage includes the checked-in production configuration, project-subpath assets
and navigation, privacy notice and footer, all three issue formats, server validation and size limits,
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
Siteverify. Fixture scripts are inert DOM elements, so singleton and existing-tag
checks exercise the actual DOM without downloading the SDK. Only test-server
responses expose the loader to check shared-promise identity; the production
source has no mock hooks or verification bypass. Coverage includes concurrent
loads, existing APIs/scripts, late loads, script failure/timeout, retries without
reinsertion, one active widget, and safe client error codes. A failed script load
requires a page reload unless its API becomes available late.
The small mock control is labelled "test fixture"; it is not a claim about Cloudflare's
actual widget appearance. Production hostname/action checks stay exact even in
private development; see worker/README.md for the limits of real test-key replies.

These are real-browser checks of the Worker fetch module and web-standard APIs,
not a claim that Wrangler/workerd or a deployed Cloudflare environment was run.
Optional local Wrangler health checks are described in [worker/README.md](../worker/README.md).
