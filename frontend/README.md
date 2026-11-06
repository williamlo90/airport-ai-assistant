# Westbridge Airport frontend

React/TypeScript/Vite foundation with reviewed public host configuration, English/Indonesian labels, and shared Tuffy/Sydney UI components. The normal entry displays the customer help homepage; other approved customer pages remain separate in [prototype/](prototype/README.md). Routing, HTTP transport and session boundary foundations are implemented. Full customer/staff page ports and their business API integration follow their own tasks.

## Customer help homepage

The root route ports the approved Sydney/Tuffy C-01 layout to React: airport hero, four help categories, local guide search/filter, and direct contact links. English is the default; Indonesian labels remain available. Topic links open `/help/new` with only the allowlisted topic and language. That destination opens C-02; opening it does not create a conversation or send a support request.

The guides contain reviewed information about using this assistant, drawn from the product specification. They contain no airport policies, parking rules, booking availability or payment status. Airport service knowledge is explicitly unavailable until its own integration is ready. Search checks only these product guides in English and Indonesian. It sends no request and keeps the query in memory across language changes, never in a URL or browser storage. Starting a support flow is independent of searching or receiving an AI answer.

The hero reuses the project-generated terminal image from the approved prototype, copied into `public/assets/airport-terminal.png`; it is not a photograph of a real airport. Tuffy and the airport mark use the existing local assets. The standard customer/staff shell remains available; only the homepage uses the full-width hero variant.

## Customer conversation

C-02 is available at `/help/new?topic=booking&lang=en` on the integrated **https://localhost:8443** origin. Choose **Start conversation** explicitly. A guest receives an anonymous session, then one create request; a current customer uses their existing session. Staff sessions cannot send customer messages. StrictMode, mounting, reload and deep links never create a conversation automatically.

Messages include an optional service category and the original user text. The form allows3,800 characters and rejects blank/null-containing input, within the API's4,000-character limit after the category prefix. A receipt marked **Saved** proves storage only; there are no simulated AI replies. The current flow retains its original messages and composer text in memory. The API returns conversation metadata, not message history: reload does not reconstruct a transcript.

Every append captures one `client_message_id` and exact body before sending. Manual retry reuses both. Create has no idempotency/lookup contract: an unknown result blocks replacement creation, including after a session recheck. An unknown message command cleared by a session check is not rebound to a new epoch; its retained text is held with an unresolved notice. A definitive403 rejection instead allows review of the legitimate text after fresh session/resource authorization. Revoked resource/404 clears private data; explicit discard removes retained input so it cannot reappear.

EN/ID interface changes preserve original text. An explicit **Use … for new messages** action updates conversation language using its server version; sending is held while languages differ. Conflict or an unknown language result requires a read-only metadata check and fresh review, never automatic PATCH replay. **Support** changes the topic inside the same conversation, carrying the story without sending a case/email. Verification and request review remain later slices.

TanStack Query manages boundary-scoped metadata reads with retry/cache lifetime disabled and cancellation passed through the auth transport. React Hook Form/Zod handle the first business form. Exact versions are pinned in the manifest/lockfile. Chat UI loads as a separate chunk. HTTP5173/5174 remain visual environments; they cannot substitute for real secure-cookie integration.

## Customer email verification

C-03 at `/help/verify` requests a code only after an explicit action in an anonymous session. A guest starts that session explicitly. Enter the8-digit code in the original browser. Challenge202 means verification was requested; the response supplies no expiry/deadline or proof of email delivery. The normal backend currently returns503 because the sender is not wired. The screen shows unavailable without claiming that a code was sent. Live delivery remains a separate integration gate.

Wrong and expired codes share a generic verification failure. Known429 Retry-After blocks another attempt, including across ordinary session checks, editing or changing the current flow; it is a request wait, not an OTP expiry countdown. Changing email drops the old challenge; resend explicitly obtains a new one. Codes stay in form memory only and are cleared on submit/session checks. Email, OTP and report text never enter URLs, browser storage or analytics.

Entering from C-02 captures a narrowly defined user-text report for that conversation and owner. A successful local verify must match the coordinator's confirmed origin/principal/proof before showing a verified-contact badge or resuming the report. An unknown result plus customer `/me` cannot prove which contact was verified. Old commands, IDs, receipts, consent, hashes and source data are not transferred. Users review/edit the retained plaintext before explicitly creating a fresh customer conversation with the reviewed text in its composer; nothing is appended or submitted to staff automatically. Repeated continuation returns the same known conversation instead of creating another.

Existing customers enter a contact already verified for their account without logout or anonymous OTP. The UI marks it as provided contact; backend validation at request confirmation remains required. A new unverified address cannot be added through this flow. Staff sessions have a separate sign-in flow. Case draft creation/review/confirmation is still FE-CASE-01, and actual email delivery is FE-AUTH-03 after MAIL-05.

## Shared component preview

While `npm run dev` is running, open **http://127.0.0.1:5173/?preview=components**. Add `&lang=id` for Indonesian. This development-only page exercises customer/staff shell layouts, buttons, input/textarea/select, field descriptions/errors, error-summary focus, native dialog, status labels, and loading/empty/error/unavailable/unknown request states. Changing the sample shell does not log in or grant staff access. Form values remain in memory across shell/language changes, and example validation sends nothing.

The preview is lazy-loaded only in development and excluded from the production application. `?preview=components` on a production build displays the normal entry instead. A known host is still required before the development preview can open. This harness uses the application router but introduces no backend connection.

Shared primitives live in `src/components/`; shells are in `src/app/shells/`; tokens/global type and reset rules are in `src/styles/`. Basic components accept state/callbacks and do not import business features or call APIs. Native dialog focus/inert behavior is checked in real browsers: jsdom tests use a small, explicitly limited dialog shim to test controlled wiring, not native focus-trap behavior.

Tuffy400/700 and the original public-domain statement are self-hosted in `public/assets/fonts/`, copied from the already verified project assets. The airport mark is project-authored SVG. No font CDN is needed. Example formatting requires explicit currency/exponent and timezone: large/unsafe minor-unit values, invalid metadata, ambiguous timestamps, and invalid calendar dates are rejected rather than guessed. The formatter currently supports safe integer minor units within±10^12 and exponent0–6; widening that range requires exact-decimal support and tests. No airport timezone or currency is inferred from language.

## Routing foundation

React Router **7.18.5** uses data mode with one browser router created outside React rendering. Version7 supports the pinned Node22.19 environment; version8.5.1 requires Node22.22 or later. Routes, nested layouts, sanitized error boundaries and navigation policy live in `src/app/routing/`.

| Path                                   | Current behavior                                                    |
| -------------------------------------- | ------------------------------------------------------------------- |
| `/`                                    | Customer help homepage with four support entries                    |
| `/help/new?topic=information`          | C-02 explicit start; topics: information, booking, payment, contact |
| `/help/conversations/:conversationId`  | UUID validation, session gate and C-02 metadata/message flow        |
| `/help/verify`                         | Customer email verification and explicit report review              |
| `/help/submissions/:draftId`           | Opaque UUID pointer retained through reload; session gate           |
| `/help/cases/:caseId`                  | UUID validation and session gate                                    |
| `/staff/login`                         | Staff sign-in placeholder                                           |
| `/staff/cases`, `/staff/cases/:caseId` | Staff capability gate, plus UUID validation on detail               |
| Other paths or invalid IDs/topics      | Generic not-found screen                                            |

The live access port uses the session coordinator and stays closed until trusted session inspection completes. URL parameters cannot grant roles or access. Controlled tests inject access states to check guard behavior; production has no role selector. These are UX gates; backend resource authorization remains mandatory. No API request, submission, automatic retry or receipt recovery is implemented by routing alone.

Open **http://127.0.0.1:5173/__dev/navigation** in development to exercise `useUnsavedChanges`: enter text, change topic, choose Keep editing or Discard changes and leave, and try browser Back/forward. Same-page language changes and staff handoff retain the story. Other navigation, including the brand link, asks before discarding registered input. Native reload/tab-close protection is best effort (`beforeunload`); text is memory-only and not recoverable after reload. Future feature forms must register with the hook and provide an explicit discard callback. Session/account boundary clearing belongs to FE-AUTH-01.

Navigation helpers copy only supported language/topic/status/view and development preview parameters, plus bounded non-sensitive fragment names. They never copy email, OTP, payload, credentials or role/tenant overrides. The application generates no URLs containing private form text. Incoming address-bar content is not a source of permission or form payload.

Development harnesses are excluded from production. The navigation harness is a routing exercise, not the C-02 customer page. Vite preview verifies SPA deep links locally; integrated HTTPS and API/asset404 checks are described under FE-ENV-01 below.

## HTTP transport foundation

`src/api/client.ts` provides `createApiClient` without React/router dependencies. The session coordinator mounts it for session inspection and boundary-owned requests; business screens integrate it with their own tasks. Requests target API-relative paths under `/api/v1`, use same-origin browser cookies, no-store caching and redirect rejection, and never supply an Origin/tenant/principal/Authorization header. Origin is browser-managed. No provider key or environment file is read.

Business calls require a captured `BoundarySnapshot`, an auth-owned `BoundaryPort`, and a feature-owned `isRelevant()` resource/sequence check. The snapshot proof is validated against the backend's43-character URL-safe CSRF contract. The transport checks epoch/boundary/proof before dispatch and after response/body parsing. It rejects old results and calls the invalidation port for an obsolete boundary or401. It never replaces the command's proof with the latest cookie. The live auth coordinator implements gate closure, cancellation, cross-tab signals and boundary-owned input reset; feature caches must subscribe to its reset contract.

Session operations use a separate, fixed endpoint/method allowlist and a coordinator-issued `SessionPermit`. The coordinator must close the business gate before bootstrap/verify/login/logout, capture the original proof and supply a current transition check. Only cookie-changing operations permit post-dispatch proof rotation; session inspection and challenge responses require unchanged proof. Bootstrap can omit a proof when no CSRF cookie exists. Authentication results require a stable `/me` recheck before opening the business gate. No automatic bootstrap, login or replay occurs on401.

Every call requires an expected HTTP status and a runtime decoder; TypeScript casts do not replace response validation. `contracts.ts` includes session, challenge and empty-response decoders; feature-specific DTOs are added with their slices.204 never parses JSON. HTML, malformed JSON, invalid DTOs and unexpected success status fail safely. Auth challenge data does not invent an expiry field.

There are **no automatic retries**, including GET and `retryable:true` responses. A caller supplies any stable Idempotency-Key explicitly; the client never generates a replacement. AbortSignal reaches fetch, timeouts abort the request (default15seconds, configurable up to120seconds), and listeners/timers are cleaned up. A dispatched mutation with network loss, timeout, cancellation, malformed success,408 or5xx has an **unknown** outcome; abort does not prove the server rolled back. Recovery/review and explicit retry belong to the feature contract.

`errors.ts` retains only normalized code, status, UUID request ID, validated case reference, server retry hint and parsed429 Retry-After. It discards raw server messages, exceptions and response bodies. English/Indonesian presentation uses controlled text, with no fabricated field-error map for the backend's generic422.403 action/CSRF errors remain distinct and do not indiscriminately erase input: the auth/feature coordinator must quarantine and recheck access before reuse.

Contract tests in `tests/contracts/client.test.ts` use synthetic fetch responses and coordinator ports. They verify transport options, late-response rejection, proof changes, status/DTO/error handling, cancellation and retry behavior. They do **not** prove live cookies, browser-generated Origin, cross-tab auth or HTTPS proxy/backend integration. Separate FE-ENV-01/FE-AUTH-01 integration checks provide that evidence.

## Session boundary foundation

`src/app/auth/` owns anonymous/customer/staff boundaries in memory. HTTPS startup performs read-only `/me` inspection and, for staff, `/staff/me`. Mounting and401 never automatically create a session, resend credentials or replay a business command. Plain HTTP previews make no auth requests.

Cookie-changing operations close the gate, cancel outstanding work, increment the epoch and signal other tabs before dispatch. Every outcome signals completion and inspects the session again. Business commands capture their original boundary and CSRF proof; a changed cookie prevents sending the old payload even without cross-tab delivery. Signals contain only transition type and random ID. Identity, contact, roles and cookies are never broadcast or persisted in browser storage.

Private routes hide during revalidation. Boundary changes clear retained input/commands; late responses cannot update the new session. A403 mutation quarantines input pending session and fresh resource inspection; revoked resource reads/404 remove it. Backend resource authorization remains mandatory. Feature modules must use `capture`, boundary-owned requests/drafts and `onReset`; independent fetch or unowned caches would bypass this contract.

For a development-only check, open **https://localhost:8443/__dev/auth** in two Chrome tabs that trust the local CA. From guest, explicitly start an anonymous session and stage a synthetic draft in tab A. Log out in tab B, then start a new anonymous session. Tab A must hide/clear its draft and block its captured old request before dispatch. Log out the synthetic session afterward. The harness never initiates customer/staff sign-in, OTP delivery, a case or email, and is excluded from production builds.

The customer verification screen uses this coordinator; staff sign-in screens follow FE-STAFF-01. Controlled login/OTP fixtures prove client boundary behavior, not delivery. Recovering a lost cross-tab end signal requires an explicit fresh recheck and never resends the original command.

## Public site configuration

`src/app/config/publicSite.ts` selects the public site using **`window.location.hostname`**, not query parameters, user-entered tenant IDs, headers, or a fallback tenant. The reviewed local-development hosts are exactly `localhost` and `127.0.0.1`; both display **Westbridge Airport — AI Assistant**. Host canonicalization matches the existing backend contract: lowercase and remove trailing dots, then validate ASCII host syntax/length. Ports belong to the origin, not this hostname lookup.

An unconfigured host displays **Site unavailable** with no site identity or language controls. The HTML title is generic until a host has been resolved. Adding a deployment hostname requires an explicit reviewed configuration change; there are no wildcards or runtime URL overrides.

English is the default. Use `?lang=id` or the language button for Indonesian; `?lang=en` selects English. Missing, unsupported, or duplicate language values use the configured default. This only changes interface labels/document language, not original customer text. Message/form preservation will be tested with those features when implemented.

The public config contains only names, supported/default languages, and a local-development marker. It contains no tenant/principal IDs, credentials, permissions, API keys, or source identity mapping. It does not select a backend tenant: the backend resolves registered hosts independently and authorizes every request. FE-CFG-01 verified the normalization contract; FE-ENV-01 adds an explicit read-only verification of the exact localhost registration below. Local branding alone does not prove that connection. No public production domain is registered by these tasks.

## Requirements

Use Node **22.19.0** and npm **11.17.0**, as recorded in `.node-version` and `package.json`. All direct dependencies are exact versions and the lockfile pins the full dependency tree. Engine mismatches fail installation rather than being ignored.

The selected jsdom29.1.1 supports this Node runtime. The current jsdom30 release requires a newer Node patch; it is not installed. jsdom runs only in the test environment, not in the production application.

## Run

### Integrated local HTTPS (FE-ENV-01)

The integrated origin is **https://localhost:8443**. A Node HTTPS gateway binds only to127.0.0.1, forwards `/api` and `/health` to the API on127.0.0.1:8001, and forwards frontend routes to Vite on127.0.0.1:5175. It accepts only the exact `localhost:8443` Host, replaces forwarded headers, preserves browser Origin/cookies/CSRF, and separates API/static404 from SPA navigation. There is no HTTP or certificate-validation bypass. Ordinary HTTP previews remain separate visual tools.

Prepare a localhost certificate using [mkcert](https://github.com/FiloSottile/mkcert) from its official release or a trusted installation. From the repository root, with mkcert on PATH:

```powershell
./frontend/scripts/prepare-local-tls.ps1
```

Alternatively pass `-MkcertPath PATH_TO_MKCERT`. The script generates a project-specific CA and localhost leaf/key in ignored `local/tls/`, refuses to overwrite existing leaf material, and **does not install trust**. Never commit or share private keys. The next command is a separate user-operated security change: inspect the generated CA fingerprint, then install its public certificate into your Windows account's Trusted Root store if you approve it:

```powershell
certutil -user -addstore Root "local/tls/ca/rootCA.pem"
```

Restart the browser if it does not pick up the new trust. Never bypass a certificate warning. Other browser/OS profiles may have separate trust stores. To revoke this project's trust later, remove this CA by its exact thumbprint from the same CurrentUser Root store; do not delete unrelated roots.

From the repository root, verify the exact backend host mapping without displaying credentials:

```powershell
uv sync --locked --project backend
uv run --locked --project backend python -m airport_assistant.local_check --expected-tenant-slug airport-demo
uv run --locked --project backend airport-assistant-api --local-proxy
```

The existing local demo uses backend slug `airport-demo`; Westbridge is its reviewed public demo branding. The verifier checks active `localhost` registration against that expected tenant through the restricted runtime login. It never creates or changes tenant records. A failure blocks integrated acceptance; do not auto-register or relabel a different tenant. Backend configuration loaders read the existing ignored root `.env` internally; the frontend/gateway never reads it. Database/auth must already be configured and enabled. `--local-proxy` requires development mode, binds127.0.0.1:8001 and trusts forwarded scheme/client information only from127.0.0.1. Normal API startup does not trust forwarded headers.

In two other terminals, from `frontend/`:

```powershell
npm run dev:integrated
```

```powershell
npm run dev:https
```

Open **https://localhost:8443**. All three processes stop with Ctrl+C. Integrated Vite disables HMR/WebSocket serving and the gateway does not forward WebSocket upgrades; refresh after code edits and restart Vite after config edits. Vite's development client may still log WebSocket connection errors; these do not indicate failed HTTPS/API requests and are absent from production output. This keeps the existing5173 visual preview independent. For local built-output checks, run `npm run build`, `npm run preview`, then use `npm run preview:https` instead of `dev:https`; stop the other gateway first. Neither gateway nor Vite preview is a production deployment server.

From `frontend/`, `node scripts/verify-local-https.mjs` exercises verified TLS, deep-link HTML, API/asset404, actual anonymous bootstrap, cookie attributes, `/me`, missing-CSRF/wrong-Origin rejection, and valid-CSRF logout204 followed by401. It trusts this project's public CA explicitly and leaves one revoked anonymous smoke session/rate-limit record; it creates no customer/case/OTP and invokes no email/model/source provider. Its cookie jar is process-local, output is sanitized, and it is **not browser cookie evidence**.

For the browser gate, use a separate browser profile without an existing local session and visit **https://localhost:8443/__dev/environment**. Click **Run connection check**. This explicit development diagnostic uses the transport session port, browser cookies and browser-generated Origin, reports only a sanitized verdict, and logs out its anonymous test session. It refuses to replace an existing session and never runs on mount. It does not implement the application auth coordinator or grant customer/staff access. The diagnostic is excluded from production. Browser trust plus successful diagnostic are required before marking FE-ENV-01 complete; HTTP fixtures, a green frontend brand or Node's explicit CA trust do not close that gate.

`npm run test:env` runs loopback HTTP fixtures for proxy boundary/routing/error behavior, without Neon or environment loading. Tests of the backend proxy flag live in `backend/tests/unit/test_local_proxy.py`. Their fixture cookies are synthetic. FE-AUTH-01 still owns cross-tab coordination, session epochs, private rendering/cache, and business integration.

From this directory:

```powershell
npm ci
npm run dev
```

Open **http://127.0.0.1:5173**. Both dev and preview bind only to loopback and fail if their port is already occupied. Stop with Ctrl+C.

## Checks

```powershell
npm run typecheck
npm run lint
npm run format:check
npm test
npm run build
```

`npm run check` runs all of these. `npm run test:watch` provides watch mode. Production output is in ignored `dist/`; inspect it using `npm run preview` at **http://127.0.0.1:5174** after building. Vite preview is a local inspection server, not a production deployment server.

The formatter and linter target the React scaffold and leave `prototype/` untouched. Typecheck includes source, tests, and tool configuration. Vitest discovers tests only under `frontend/tests/`; cross-component E2E remains at repository `tests/e2e/` and is not implemented by this bootstrap.

## Boundaries

Normal visual development and offline tests need no backend, database, provider, credentials, or `.env`. The integrated FE-ENV-01 smoke explicitly needs the configured backend/database. Vite and Vitest disable environment-file loading. The ordinary application entry mounts the auth coordinator on configured hosts and inspects sessions only over HTTPS. Business screens and AI behavior are not yet connected; development diagnostics remain separate. The prototype at port4173 remains a separate static design reference; it is not imported into the React app or bundled by its entry.

Private planning documents remain local under `docs/frontend/`, outside Git. Features and tests are added alongside implementation rather than through empty configuration files or unused libraries.
