# Westbridge Airport frontend

React/TypeScript/Vite foundation with reviewed public host configuration and English/Indonesian labels. This entry currently displays a development readiness screen; the approved customer pages remain separate in [prototype/](prototype/README.md). Shared Tuffy/Sydney components, routing, authentication, and API integration follow their own tasks.

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

No backend, database, provider, credentials, or `.env` is needed for this stage. Vite and Vitest disable environment-file loading. There is no runtime API client or simulated AI in this entry. The prototype at port4173 remains a separate static design reference; it is not imported into the React app or bundled by its entry.

Private planning documents remain local under `docs/frontend/`, outside Git. Features and tests are added alongside implementation rather than through empty configuration files or unused libraries.
