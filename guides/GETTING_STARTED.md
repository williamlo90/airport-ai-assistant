# Getting started

[Back to the project](../README.md) | [Verification](VERIFICATION.md)

Run commands from the repository root unless a code block changes directory. Use Python
3.13 and `uv` for Python applications; the frontend pins Node and npm in its package metadata.

## Design preview

```powershell
python -m http.server 4173 --bind 127.0.0.1 --directory frontend/prototype
```

Open [the local preview](http://127.0.0.1:4173). No credentials or dependencies are needed.
The [product tour](PRODUCT_TOUR.md) explains its fixture-based screens. Stop with Ctrl+C.

## React application

Use Node 22.19.0 and npm 11.17.0, matching `frontend/.node-version` and `frontend/package.json`.

```powershell
cd frontend
npm ci
npm run dev
```

Open [the React development server](http://127.0.0.1:5173). This checkout provides the
customer shell, language controls, shared components, and routing foundations. The separate
static preview presents the fuller customer journey. Stop with Ctrl+C.

## API bootstrap

Install `uv` using its [official installation guide](https://docs.astral.sh/uv/getting-started/installation/).
From the repository root:

```powershell
uv sync --locked --project backend
if (!(Test-Path .env)) { Copy-Item .env.example .env }
uv run --locked --project backend airport-assistant-api --check-config
uv run --locked --project backend airport-assistant-api
```

Open [development API documentation](http://127.0.0.1:8000/docs) or
[process health](http://127.0.0.1:8000/health/live). The example configuration disables database,
authentication, and source integration, so bootstrap needs no external credentials. Stop with
Ctrl+C. Copy the example only when a local environment file does not already exist.

For database-enabled features, use a dedicated development database and follow the
[backend setup guide](../backend/README.md). Keep runtime and migration credentials separate.

## Booking reference service

The reference service is independently packaged. Setup creates ignored local signing keys and
a local SQLite database; it does not require credentials for the application database.

```powershell
uv sync --locked --project reference_service
uv run --locked --project reference_service airport-reference-setup
uv run --locked --project reference_service airport-reference-api --check-config
uv run --locked --project reference_service airport-reference-api
```

Use a separate terminal at the repository root to run the signed demo client:

```powershell
uv run --locked --project reference_service airport-reference-demo booking PK-A-001
```

See the [reference-service guide](../reference_service/README.md) for actor scopes and API
contracts. Stop the service with Ctrl+C.

## Local data

Environment files, private planning documents under `docs/`, local databases, and raw artifacts
remain ignored. Use the supplied reference records. Keep signing keys and credentials out of
the browser bundle or tracked files.
