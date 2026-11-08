# Verification guide

[Back to the project](../README.md) | [Getting started](GETTING_STARTED.md)

Choose checks that match the change. Documentation edits use the repository checker; application
changes use the relevant tests. Execute validation jobs sequentially. Keep Python tests serial
and frontend test files sequential, with at most two workers. Do not start application servers,
containers, or external services merely to review documentation.

## Repository documentation

From the repository root, with Python alone:

```powershell
python scripts/check_repository.py
```

This checks local Markdown file targets and required review entry points in public
documentation. It does not contact remote URLs or start services.

## Credential-free backend checks

```powershell
uv sync --locked --project backend
uv run --locked --project backend pytest backend/tests/contracts backend/tests/unit
```

These suites inspect domain rules, authentication boundaries, configuration behavior, source
adapters, and dependency separation using controlled inputs.

## Reference-service checks

```powershell
uv sync --locked --project reference_service
uv run --locked --project reference_service pytest reference_service/tests
```

The suite uses synthetic source data and temporary local resources. It needs no airport vendor
or model-provider credentials.

## Frontend checks

From `frontend/`, after `npm ci`:

```powershell
npm run check
```

This runs TypeScript, ESLint, formatting, component tests, and the production build in sequence.
Vitest limits worker concurrency and disables parallel execution of test files.

## Database-connected checks

PostgreSQL integration scenarios live in `backend/tests/integration/`. Use a dedicated test
database, configured runtime and migration credentials, and the explicit setup and opt-in
instructions in the [backend guide](../backend/README.md). These checks can mutate test data;
they are separate from credential-free CI.

## GitHub quality gate

The [workflow](../.github/workflows/quality-gate.yml) checks documentation on pull requests or
manual dispatch. Enable the `application_checks` option during manual dispatch to run the
application suites sequentially. Repository permissions are read-only and provider secrets
are not supplied. It does not trigger on ordinary pushes, provision cloud infrastructure, or run
database-connected acceptance scenarios. A workflow definition describes verification to run;
the Actions result records whether a particular revision passed.
