# Westbridge Airport frontend

[Project overview](../README.md) | [Product tour](../guides/PRODUCT_TOUR.md) | [Verification](../guides/VERIFICATION.md)

React, TypeScript, and Vite provide the customer shell, bilingual interface labels, shared
components, and routing foundations. The independently runnable
[design preview](prototype/README.md) presents the customer journey.

## Run

Use Node 22.19.0 and npm 11.17.0, as pinned in `.node-version` and `package.json`.
From this directory:

```powershell
npm ci
npm run dev
```

Open [the local application](http://127.0.0.1:5173). The server binds to loopback and fails
if its port is already occupied. Stop with Ctrl+C.

To inspect shared components, open
[the component preview](http://127.0.0.1:5173/?preview=components).
Use `lang=id` for Indonesian or `lang=en` for English.

## Application structure

| Area         | Responsibility                                                       |
| ------------ | -------------------------------------------------------------------- |
| `src/app/`   | Application composition, routing, and public site configuration.     |
| `src/`       | Shared UI, interface language, and application code.                 |
| `tests/`     | Component, configuration, and routing checks.                        |
| `prototype/` | Independent static product design; not bundled into the React entry. |

Public site identity comes from reviewed hostname configuration. An unknown hostname displays
an unavailable state rather than selecting a fallback tenant. Public configuration contains
branding and supported languages; backend authorization owns tenant and user permissions.
Changing the interface language preserves original customer text.

## Checks

```powershell
npm run typecheck
npm run lint
npm run format:check
npm test
npm run build
```

`npm run check` runs these commands in sequence. Vitest limits worker concurrency and runs
test files sequentially. Keep the same limits when running targeted tests.

## Build preview

After building:

```powershell
npm run preview
```

Open [the local build preview](http://127.0.0.1:5174). Output stays in ignored `dist/`.
Stop with Ctrl+C. This command serves the build for local inspection.

## Configuration and assets

The scaffold disables environment-file loading in Vite and Vitest. It can be reviewed without
database credentials or paid services. Keep credentials out of browser configuration.

The locally served Tuffy font retains its [license statement](public/assets/fonts/LICENSE.txt).
Private design notes and runtime artifacts stay outside tracked source.
