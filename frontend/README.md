# Westbridge Airport frontend

React/TypeScript/Vite foundation. This entry currently displays a development readiness screen; the approved customer pages remain separate in [prototype/](prototype/README.md). Routing, host branding configuration, shared Tuffy/Sydney components, authentication, and API integration follow their own tasks.

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
