# Airport AI Assistant

A multilingual customer service assistant for airport visitors and passengers. It combines guided support, AI-assisted conversations, and human handoff for information requests and issues involving existing parking and lounge bookings.

## Product scope

- Airport information in Indonesian and English.
- Read-only access to authorized booking and transaction data.
- Confirmed submission and tracking of support cases.
- Staff chat and direct email follow-up on the same case.
- Tenant isolation and role-based access for customers, customer service, supervisors, and administrators.

Booking creation, payment processing, refund execution, and delegation management are outside the MVP.

## Project documents

| Document | Purpose |
| --- | --- |
| [Product contract](PRODUCT_CONTRACT.md) | User needs, scope, business rules, and quality requirements. |
| [Architecture](ARCHITECTURE.md) | System boundaries, data flows, access controls, and deployment design. |
| [Data and API contracts](DATA_AND_API_CONTRACTS.md) | Domain entities, API behavior, confirmation, and state transitions. |
| [Test plan](TEST_PLAN.md) | Planned verification, acceptance gates, and evidence requirements. |
| [Backend architecture](docs/backend/backend_architecture.md) | Backend responsibilities, module dependencies, and request flow. |
| [Database schema](docs/backend/database_schema.md) | PostgreSQL tables, field types, keys, relationships, constraints, indexes, and isolation rules. |
| [Backend task plan](docs/backend/task_plan.md) | Implementation order, prerequisites, completion criteria, review evidence, and ownership exercises. |

## Implementation status

The repository contains the reviewed planning documents, the agreed folder scaffold, and an executable FastAPI bootstrap (BE-01) with process liveness, structured errors, request IDs, and input limits (BE-02), plus service dependency wiring and executable import boundaries (BE-03). See [backend setup](backend/README.md) for locked installation, configuration, startup, and test commands. The worker remains a placeholder; authentication, AI, and business workflows are not implemented yet. DB-01 provides opt-in database connections verified against Neon for pooled/direct access, TLS, commit, rollback, and pool reuse. DB-02 installs nine tenant/identity foundation tables through versioned Alembic migrations. DB-03 adds a restricted runtime login, forced tenant/identity RLS, transaction-local context, and protection against self-escalation. The full suite passes 192 tests including live connection, migration, and actual-login isolation checks. Case/workflow schema and authentication APIs remain future tasks. Document review is separate from implementation testing.

The architecture uses a modular backend and Neon PostgreSQL, with a VPS/Kubernetes deployment direction. Model and email providers are not selected. Booking and transaction integrations initially use a separate reference service with test data.

## Repository structure

| Directory | Responsibility |
| --- | --- |
| `frontend/` | Customer and staff UI, shared components, API client, and translations. |
| `backend/` | Modular API and worker sharing domain logic, migrations, and backend tests. |
| `reference_service/` | Separate read-only booking/transaction source API with synthetic fixtures and its own data. |
| `tests/e2e/` | End-to-end scenarios crossing application boundaries. |
| `evaluations/` | Versioned AI/RAG datasets, scoring rubrics, and evaluation runners. |
| `scripts/` | Setup, seeding, checks, and operational helpers. |
| `deploy/` | Container, Kubernetes, and VPS deployment configuration. |
| `docs/decisions/` | Significant technical decisions and their rationale. |
| `docs/runbooks/` | Deployment, rollback, restore, and incident procedures. |
| `.github/workflows/` | CI workflows once executable checks are implemented. |

Provider adapters belong in `backend/src/airport_assistant/integrations/`; business rules belong in `modules/`. Evaluation datasets are separate from operational fixtures. Runtime logs, uploads, backups, and raw test outputs stay outside tracked source; publish only deliberately selected, sanitized evidence. Remove a directory's `.gitkeep` when adding its first real file.

## Local configuration

`.env.example` supplies the API bootstrap settings and reserves placeholders for later integrations. Copy it to `.env` without overwriting an existing local file. Bootstrap without database remains credential-free. DB-01 separately loads runtime and migration URLs; see backend setup for opt-in connection checks. Email/model/source placeholders remain reserved for their features.

Never commit `.env`, API keys, database credentials, private keys, real customer data, or unredacted provider payloads. Use synthetic fixtures for development and testing.
