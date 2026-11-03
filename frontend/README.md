# Westbridge Airport frontend

React/TypeScript/Vite foundation with reviewed public host configuration, English/Indonesian labels, and shared Tuffy/Sydney UI components. The normal entry displays a readiness screen inside the customer shell; the approved customer pages remain separate in [prototype/](prototype/README.md). Routing foundations are implemented; full page ports, authentication, and API integration follow their own tasks.

## Shared component preview

While `npm run dev` is running, open **http://127.0.0.1:5173/?preview=components**. Add `&lang=id` for Indonesian. This development-only page exercises customer/staff shell layouts, buttons, input/textarea/select, field descriptions/errors, error-summary focus, native dialog, status labels, and loading/empty/error/unavailable/unknown request states. Changing the sample shell does not log in or grant staff access. Form values remain in memory across shell/language changes, and example validation sends nothing.

The preview is lazy-loaded only in development and excluded from the production application. `?preview=components` on a production build displays the normal entry instead. A known host is still required before the development preview can open. This harness uses the application router but introduces no backend connection.

Shared primitives live in `src/components/`; shells are in `src/app/shells/`; tokens/global type and reset rules are in `src/styles/`. Basic components accept state/callbacks and do not import business features or call APIs. Native dialog focus/inert behavior is checked in real browsers: jsdom tests use a small, explicitly limited dialog shim to test controlled wiring, not native focus-trap behavior.

Tuffy400/700 and the original public-domain statement are self-hosted in `public/assets/fonts/`, copied from the already verified project assets. The airport mark is project-authored SVG. No font CDN is needed. Example formatting requires explicit currency/exponent and timezone: large/unsafe minor-unit values, invalid metadata, ambiguous timestamps, and invalid calendar dates are rejected rather than guessed. The formatter currently supports safe integer minor units within±10^12 and exponent0–6; widening that range requires exact-decimal support and tests. No airport timezone or currency is inferred from language.

## Routing foundation

React Router **7.18.5** uses data mode with one browser router created outside React rendering. Version7 supports the pinned Node22.19 environment; version8.5.1 requires Node22.22 or later. Routes, nested layouts, sanitized error boundaries and navigation policy live in `src/app/routing/`.

| Path                                   | Current behavior                                                    |
| -------------------------------------- | ------------------------------------------------------------------- |
| `/`                                    | Customer shell and readiness screen                                 |
| `/help/new?topic=information`          | Support placeholder; topics: information, booking, payment, contact |
| `/help/conversations/:conversationId`  | UUID validation and session gate                                    |
| `/help/verify`                         | Email verification placeholder                                      |
| `/help/submissions/:draftId`           | Opaque UUID pointer retained through reload; session gate           |
| `/help/cases/:caseId`                  | UUID validation and session gate                                    |
| `/staff/login`                         | Staff sign-in placeholder                                           |
| `/staff/cases`, `/staff/cases/:caseId` | Staff capability gate, plus UUID validation on detail               |
| Other paths or invalid IDs/topics      | Generic not-found screen                                            |

The access port defaults to **unavailable** until FE-AUTH-01 supplies trusted session state. URL parameters cannot grant roles or access. Controlled tests inject access states to check guard behavior; production has no role selector. These are UX gates; backend resource authorization remains mandatory. No API request, submission, automatic retry or receipt recovery is implemented by routing alone.

Open **http://127.0.0.1:5173/__dev/navigation** in development to exercise `useUnsavedChanges`: enter text, change topic, choose Keep editing or Discard changes and leave, and try browser Back/forward. Same-page language changes and staff handoff retain the story. Other navigation, including the brand link, asks before discarding registered input. Native reload/tab-close protection is best effort (`beforeunload`); text is memory-only and not recoverable after reload. Future feature forms must register with the hook and provide an explicit discard callback. Session/account boundary clearing belongs to FE-AUTH-01.

Navigation helpers copy only supported language/topic/status/view and development preview parameters, plus bounded non-sensitive fragment names. They never copy email, OTP, payload, credentials or role/tenant overrides. The application generates no URLs containing private form text. Incoming address-bar content is not a source of permission or form payload.

Both development harnesses are excluded from production. The navigation harness is a routing exercise, not the C-02 customer page. Vite preview verifies SPA deep links locally; HTTPS proxy and API/asset404 separation remain FE-ENV-01 responsibilities.

## HTTP transport foundation

`src/api/client.ts` provides `createApiClient` without React/router dependencies. It is not mounted into the UI yet. Requests target API-relative paths under `/api/v1`, use same-origin browser cookies, no-store caching and redirect rejection, and never supply an Origin/tenant/principal/Authorization header. Origin is browser-managed. No provider key or environment file is read.

Business calls require a captured `BoundarySnapshot`, an auth-owned `BoundaryPort`, and a feature-owned `isRelevant()` resource/sequence check. The snapshot proof is validated against the backend's43-character URL-safe CSRF contract. The transport checks epoch/boundary/proof before dispatch and after response/body parsing. It rejects old results and calls the invalidation port for an obsolete boundary or401. It never replaces the command's proof with the latest cookie. The auth coordinator must implement gate closure, cancellation, cross-tab signals and cache/input reset in FE-AUTH-01; an injected port is not that implementation.

Session operations use a separate, fixed endpoint/method allowlist and a coordinator-issued `SessionPermit`. The coordinator must close the business gate before bootstrap/verify/login/logout, capture the original proof and supply a current transition check. Only cookie-changing operations permit post-dispatch proof rotation; session inspection and challenge responses require unchanged proof. Bootstrap can omit a proof when no CSRF cookie exists. Authentication results require a stable `/me` recheck before opening the business gate. No automatic bootstrap, login or replay occurs on401.

Every call requires an expected HTTP status and a runtime decoder; TypeScript casts do not replace response validation. `contracts.ts` includes session, challenge and empty-response decoders; feature-specific DTOs are added with their slices.204 never parses JSON. HTML, malformed JSON, invalid DTOs and unexpected success status fail safely. Auth challenge data does not invent an expiry field.

There are **no automatic retries**, including GET and `retryable:true` responses. A caller supplies any stable Idempotency-Key explicitly; the client never generates a replacement. AbortSignal reaches fetch, timeouts abort the request (default15seconds, configurable up to120seconds), and listeners/timers are cleaned up. A dispatched mutation with network loss, timeout, cancellation, malformed success,408 or5xx has an **unknown** outcome; abort does not prove the server rolled back. Recovery/review and explicit retry belong to the feature contract.

`errors.ts` retains only normalized code, status, UUID request ID, validated case reference, server retry hint and parsed429 Retry-After. It discards raw server messages, exceptions and response bodies. English/Indonesian presentation uses controlled text, with no fabricated field-error map for the backend's generic422.403 action/CSRF errors remain distinct and do not indiscriminately erase input: the auth/feature coordinator must quarantine and recheck access before reuse.

Contract tests in `tests/contracts/client.test.ts` use synthetic fetch responses and coordinator ports. They verify transport options, late-response rejection, proof changes, status/DTO/error handling, cancellation and retry behavior. They do **not** prove live cookies, browser-generated Origin, cross-tab auth or HTTPS proxy/backend integration. Those remain FE-ENV-01/FE-AUTH-01.

## Public site configuration

`src/app/config/publicSite.ts` selects the public site using **`window.location.hostname`**, not query parameters, user-entered tenant IDs, headers, or a fallback tenant. The reviewed local-development hosts are exactly `localhost` and `127.0.0.1`; both display **Westbridge Airport — AI Assistant**. Host canonicalization matches the existing backend contract: lowercase and remove trailing dots, then validate ASCII host syntax/length. Ports belong to the origin, not this hostname lookup.

An unconfigured host displays **Site unavailable** with no site identity or language controls. The HTML title is generic until a host has been resolved. Adding a deployment hostname requires an explicit reviewed configuration change; there are no wildcards or runtime URL overrides.

English is the default. Use `?lang=id` or the language button for Indonesian; `?lang=en` selects English. Missing, unsupported, or duplicate language values use the configured default. This only changes interface labels/document language, not original customer text. Message/form preservation will be tested with those features when implemented.

The public config contains only names, supported/default languages, and a local-development marker. It contains no tenant/principal IDs, credentials, permissions, API keys, or source identity mapping. It does not select a backend tenant: the backend resolves registered hosts independently and authorizes every request. This task verifies normalization and provisioning contracts against backend source, **not live database registration**. Before integrated deployment, FE-ENV-01 must verify the exact HTTPS hostname is registered to the intended tenant; local branding does not prove a backend connection. No public production domain is registered by this task.

## Requirements

Use Node **22.19.0** and npm **11.17.0**, as recorded in `.node-version` and `package.json`. All direct dependencies are exact versions and the lockfile pins the full dependency tree. Engine mismatches fail installation rather than being ignored.

The selected jsdom29.1.1 supports this Node runtime. The current jsdom30 release requires a newer Node patch; it is not installed. jsdom runs only in the test environment, not in the production application.

## Run

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

No backend, database, provider, credentials, or `.env` is needed for this stage. Vite and Vitest disable environment-file loading. The API client foundation is not connected to this entry; there is no simulated AI. The prototype at port4173 remains a separate static design reference; it is not imported into the React app or bundled by its entry.

Private planning documents remain local under `docs/frontend/`, outside Git. Features and tests are added alongside implementation rather than through empty configuration files or unused libraries.
