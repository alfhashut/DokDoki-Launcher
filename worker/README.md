# DokDoki request Worker — Part 3

This plain JavaScript Cloudflare Worker accepts website reports and creates public
GitHub Issues in **alfhashut/DokDoki-Launcher**. It has no database, accounts,
cookies, analytics, IP collection, or submission storage. The website remains on
GitHub Pages; the Worker is a separate API.

**Do not deploy or enable public submissions in this phase. Part 4 will provision
real credentials and deliberately enable the service.** The final architecture is
GitHub Pages → Turnstile → Cloudflare Worker → GitHub Issues; nothing is hosted on
a local machine. Loopback servers in this repository are development/test tools only.
CORS is a browser restriction, not authentication. Turnstile is verified by the
Worker independently of the website. Production remains disabled by default.

## API

- `GET /health` → `200 {"ok":true}`. No configuration or secrets are returned.
- `POST /api/report` accepts `application/json` from an allowed Origin.
- `OPTIONS /api/report` handles preflight for POST and Content-Type only.
- Other methods return 405; unknown paths return 404. Missing, `null`, and
  unconfigured origins on the report endpoint return 403.
- Success: `201 {"ok":true,"requestNumber":43,"status":"received"}`.
- Validation: 400 with `error: "validation_error"`, a safe `message`, and an
  optional schema field key in `field`. Oversized bodies return 413; wrong content
  type returns 415. Disabled submissions return 503 with
  `error: "submissions_disabled"`. Missing credentials return a generic 503;
  upstream failures return a generic 502 with `error: "submission_failed"`.
- Verification failures return the same 403 response in every case:
  `{"ok":false,"error":"verification_failed","message":"Verification failed. Please try again."}`.
  Missing secrets, expired/invalid/replayed tokens, hostname/action mismatches,
  malformed Siteverify replies, HTTP errors, and network failures are not
  distinguished in the public response.
- Responses use `Cache-Control: no-store`. Auth errors, upstream bodies, exceptions,
  configuration, credentials, and Cloudflare request metadata are never returned.

All payloads have a `type` and the following flat string fields. Unknown fields,
non-string values, invalid enum values, control characters, and excessive lengths
are rejected. Required values are trimmed, line endings become LF, and Unicode is
normalized to NFC. Limits apply before and after normalization. URLs must be full
HTTP(S) URLs without whitespace or embedded login details; they are never fetched.
Total request body limit: **64 KiB**, enforced on the stream as well as Content-Length.
All three types also require `turnstileToken`, a nonempty string of at most 2048
characters without whitespace. It is verification input only and never becomes
issue content or metadata. Clients cannot supply a Turnstile action override.

| Type | Required fields | Optional fields |
| --- | --- | --- |
| `bug` | `dokdokiVersion` (80), `operatingSystem` (`windows` / `linux`), `whatHappened` (10–5000), `stepsToReproduce` (10–5000) | `additionalInformation` (5000) |
| `mod-request` | `modName` (200), `sourceUrl` (2048) | `modAuthor` (200), `modVersion` (80), `additionalInformation` (5000) |
| `removal-request` | `modName` (200), `sourceUrl` (2048), `reason` (10–5000), `rightsHolder` (`yes` / `no`) | `proofUrl` (2048), `additionalInformation` (5000) |

Numbers in parentheses are maximum JavaScript string lengths unless a range is
shown. Names, authors, and versions must be single-line text.

## Exact issue formatting

Titles use fixed prefixes: `[Bug] `, `[Mod Request] `, or `[Removal Request] `.
Bug summaries come from `whatHappened`; other summaries come from `modName`.
Whitespace is collapsed, angle brackets are removed, and the summary is limited
to 100 Unicode code points. Callers cannot choose a title prefix, repository,
labels, issue state, assignees, or other GitHub metadata.

Each body consists of the following `##` headings, in this order, with a blank
line and an indented literal code block for the submitted value. Each line of a
value gets four spaces, so submitted Markdown/HTML cannot add headings or hidden
metadata. Empty optional fields consistently become `Not provided`.

| Type | Body headings, in order | Requested labels |
| --- | --- | --- |
| Bug | DokDoki version; Operating system; What happened?; Steps to reproduce; Additional information | `bug`, `status:received` |
| Mod | Mod name; Source; Author; Version; Additional information | `mod-request`, `status:received` |
| Removal | Mod name; Source; Reason; Author / rights holder; Proof / relevant URL; Additional information | `removal-request`, `status:received` |

Operating system values become `Windows` / `Linux`; rights-holder values become
`Yes` / `No`. No requester IP, user agent, CF headers, hidden HTML, or generated
machine metadata is appended. Public visibility is disclosed before an enabled
form can be submitted, including for removal reasons and proof URLs.

The Worker first creates the issue **without labels**, exactly once. After a
confirmed 201 and a valid issue number, `ctx.waitUntil` checks each fixed label
with `GET /labels/{name}` and posts only existing, unarchived labels to
`POST /issues/{number}/labels`. It never calls the repository label-creation API.
Missing labels, permissions, timeouts, or label API errors produce fixed server
log event names and do not turn a created issue into a failed submission. Logs
contain no report content, credential, upstream body, IP, or exception details.
If labels cannot be applied, status.html still displays the issue's open state.

The Worker sends `Accept: application/vnd.github+json`,
`X-GitHub-Api-Version: 2026-03-10`, an identifying User-Agent, and the secret as a
Bearer credential only to the fixed GitHub API origin. Redirects fail closed;
GitHub calls time out after 10 seconds. Issue creation is never retried
automatically. A lost response may still mean an issue was created; check status
before manually trying again. Durable idempotency is not implemented.

## Turnstile verification and token lifecycle

After validating the report schema and before **any** GitHub API request, the
Worker sends only `{secret, response}` to
`https://challenges.cloudflare.com/turnstile/v0/siteverify`. `secret` comes only
from `env.TURNSTILE_SECRET_KEY`; `response` is the submitted token. No requester
IP, `remoteip`, CF headers, or other requester metadata is forwarded. Redirects
are rejected and verification times out after eight seconds. Verification is
never automatically retried. A verified request must satisfy all of:

- `success === true` (a string or other truthy value is insufficient).
- `hostname === "alfhashut.github.io"` exactly, including when a local origin is
  explicitly allowed by CORS. There is no hostname bypass for development.
- `action` matches the validated report type using this server-owned mapping:

| Report type | Expected action |
| --- | --- |
| `bug` | `report_bug` |
| `mod-request` | `request_mod` |
| `removal-request` | `request_removal` |

Actions use only permitted letters/underscores and are shorter than 32 characters.
Siteverify alone determines token validity and single-use enforcement. The
Worker does not store tokens, log them or raw verification replies, include them
in responses/Issues, or persist rate-limit/IP data. Verification failures log
only the fixed event name `report_verification_failed`.

On the website, one widget is rendered explicitly for the visible form using
the official script URL and `theme: "auto"`, compact sizing, and no generated
hidden response field. Switching forms removes the previous widget and token.
The callback keeps the token only in transient JavaScript memory. Submit stays
disabled until a challenge succeeds, and the submit handler also enforces this
gate. Tokens older than five minutes are rejected locally even if an expiry
callback was delayed; the server still verifies every attempted token.

The token is removed from client state when sending begins. Every failed or
uncertain submission resets the challenge and requires a fresh callback before
another attempt. Expiry and interactive timeout callbacks explain the expiry and
reset the widget. Script/widget failures offer a keyboard-accessible Retry
verification button rather than an automatic error loop. Success removes the
widget, clears its token, and keeps the form locked. Pagehide clears widgets and
tokens; back/forward restoration requires verification again. Tokens never enter
storage, logs, hidden inputs, or status URLs.

## Credential and configuration

The required Worker secrets are **TURNSTILE_SECRET_KEY** and **GITHUB_TOKEN**.
Eventually use a fine-grained GitHub token
scoped to **only alfhashut/DokDoki-Launcher**, with **Issues: Read and write**.
No broader repository permissions are needed. Actual label application also
depends on the token owner's role in the repository; failure is nonfatal.

Never put the credential in HTML, website JavaScript, Wrangler vars, logs, or
tracked files. Use Cloudflare's secret facility in Part 4; do not provision or
deploy it as part of this phase. Local secrets belong in ignored `.dev.vars`.

Non-secret Wrangler variables:

- `SUBMISSIONS_ENABLED`: only the exact string `true` permits submissions. The
  checked-in value is `false`; missing and other values fail closed.
- `ALLOWED_ORIGINS`: comma-separated exact origins. Default:
  `https://alfhashut.github.io` (no `/DokDoki-Launcher/` path). Add each local
  scheme/host/port explicitly. HTTP is accepted only for loopback hosts; HTTPS
  origins must also match exactly. Wildcards, paths, and credentials are rejected.
  The response echoes only an allowed origin and varies by Origin. It does not
  allow credentials or wildcard POST origins.

`workers_dev` and `preview_urls` are also disabled and no routes or deployment
workflow are supplied. Merging this directory cannot activate the API.

## Local development and testing

Automated checks mock Siteverify, the widget SDK, and GitHub. They use Cloudflare's
documented test site key/token/secret and a dummy GitHub credential. They make no
real Siteverify requests, never create real issues, and need no Cloudflare account,
real credential, Node, npm, or Wrangler.
See [the test instructions](../tests/README.md).

For optional private Worker-runtime development, use an already-installed current
Wrangler CLI. From `worker/`:

```sh
cp .dev.vars.example .dev.vars
wrangler dev --ip 127.0.0.1 --port 8787
```

The copied file contains only secret placeholders and stays disabled. `GET /health` can
be checked without a token. Do not use `--remote`, deploy, or put a live credential
in the test setup. Use the mocked suite for submission checks. A local Worker is
not part of the final hosting architecture.

Official test values (test-only, not real production keys):

| Purpose | Official value |
| --- | --- |
| Visible widget that always passes | `1x00000000000000000000AA` |
| Siteverify test secret that passes | `1x0000000000000000000000000000000AA` |
| Siteverify test secret for expired/duplicate responses | `3x0000000000000000000000000000000AA` |
| Dummy token | `XXXX.DUMMY.TOKEN.XXXX` |

An optional real-widget browser check may use the official site key above with a
private loopback test page. **A real test-key Siteverify response may report
localhost / a test action and will fail this Worker's strict production checks.**
Do not weaken hostname/action checks or add a bypass. The automated suite mocks
those fields to verify acceptance of the exact production hostname/actions and
rejection of everything else. Production secrets reject dummy tokens.

## Website configuration

`website/js/requests.js` keeps `SUBMISSIONS_ENABLED = false`, `API_BASE_URL = ""`,
and `TURNSTILE_SITE_KEY = ""`. No Turnstile script is loaded and no widget is
rendered unless all three are configured. Production ignores every local query
override, so merging the integration cannot activate the form. The public site
key is not a secret; both private keys belong exclusively in Worker secrets.

Only on `localhost`, `127.0.0.1`, or `[::1]`, a private test can opt in with:

```text
http://127.0.0.1:8000/DokDoki-Launcher/requests.html?submissions=1&api=http%3A%2F%2F127.0.0.1%3A8787&sitekey=1x00000000000000000000AA
```

This is a local test URL, not a checked-in production API endpoint. The base URL
must have no credentials, query, or fragment; `/api/report` is appended to it.
The test server described in tests/README.md supplies the requested Pages subpath.

When enabled, the website validates every field, sends only the schema fields,
locks all submit buttons and the picker during the request, and shows Submitting…
It renders success only for a valid successful response with a positive request
number and `received` status. The success link is relative:
`status.html?q=NUMBER`. It never links the success result to GitHub. A successful
form stays locked for that page visit. Reloading never submits automatically;
nothing is queued or persisted. Validation and backend failures permit a manual
retry; uncertain network results advise checking status first.

## Future Part 4 production setup — do not perform during Part 3

In Cloudflare Dashboard, create **one Turnstile widget in Managed mode** named
for DokDoki Launcher. Its authorized hostname must be **alfhashut.github.io**.
Enter only that hostname: no `https://`, `/DokDoki-Launcher/`, other paths, ports,
or wildcard. Keep pre-clearance disabled; clearance cookies are not needed.
Managed mode is selected in the dashboard, not by an invented client `mode` option.

| Value | Where it belongs |
| --- | --- |
| `TURNSTILE_SITE_KEY` | Public website config in `website/js/requests.js` |
| `TURNSTILE_SECRET_KEY` | Private Cloudflare Worker secret only |
| `GITHUB_TOKEN` | Private Cloudflare Worker secret only; repository-scoped Issues: Read and write |
| `API_BASE_URL` | Public website config, final HTTPS Worker base URL without `/api/report` |
| `ALLOWED_ORIGINS` | Worker non-secret variable: `https://alfhashut.github.io` |
| `SUBMISSIONS_ENABLED` | Keep false in both places now; enable deliberately in Part 4 |

**TURNSTILE_SECRET_KEY and GITHUB_TOKEN must never be committed.** Public site
keys may be committed when deliberately configured later. Real credential
provisioning, hosted Worker URL/routing, deployment, and public enablement are
left for Part 4. The Worker additionally enforces the exact hostname and expected
action regardless of dashboard hostname settings or client configuration.
No local service is required by the finished production architecture.

CORS is not authentication, and Turnstile is not durable issue idempotency or a
complete rate-limit policy. Duplicate-click guards remain, and no database/KV,
Durable Object, account system, IP storage, or persistent IP blocking was added.

Official documentation checked on 2026-10-22:

- [Cloudflare fetch handler](https://developers.cloudflare.com/workers/runtime-apis/handlers/fetch/)
- [Cloudflare secrets and local variables](https://developers.cloudflare.com/workers/configuration/secrets/)
- [Wrangler configuration](https://developers.cloudflare.com/workers/wrangler/configuration/)
- [GitHub create an issue](https://docs.github.com/en/rest/issues/issues#create-an-issue)
- [GitHub label endpoints](https://docs.github.com/en/rest/issues/labels)
- [Turnstile client rendering](https://developers.cloudflare.com/turnstile/get-started/client-side-rendering/)
- [Turnstile widget configuration](https://developers.cloudflare.com/turnstile/get-started/client-side-rendering/widget-configurations/)
- [Turnstile Siteverify](https://developers.cloudflare.com/turnstile/get-started/server-side-validation/)
- [Turnstile testing](https://developers.cloudflare.com/turnstile/troubleshooting/testing/)
- [Turnstile hostname management](https://developers.cloudflare.com/turnstile/additional-configuration/hostname-management/)
