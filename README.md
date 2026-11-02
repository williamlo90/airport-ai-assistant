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
| [Customer authentication](docs/backend/authentication.md) | Session lifecycle, email challenges, cookie/CSRF protection, setup, and delivery limits. |
| [Staff authentication](docs/backend/staff_authentication.md) | Provisioned staff login, live membership scopes, credential rotation, and password worker limits. |
| [Shared authorization](docs/backend/authorization.md) | Trusted request identities, exact object binding, ownership and membership scopes, and integration requirements. |
| [Reference source](reference_service/README.md) | Standalone source setup, signed API protocol, fictional actors/data, demo client, and tests. |
| [Migration workflow](docs/backend/migration_workflow.md) | Revision creation, constraints/RLS, cyclic FKs, expand/contract, and upgrade verification. |
| [Backend task plan](docs/backend/task_plan.md) | Implementation order, prerequisites, completion criteria, review evidence, and ownership exercises. |

## Implementation status

The backend provides a FastAPI bootstrap with structured errors, request IDs, input limits, service wiring, and executable module boundaries (BE-01–03). Neon PostgreSQL connections, versioned migrations, restricted runtime roles, forced tenant/identity RLS, and guarded populated upgrades are implemented (DB-01–04). See [backend setup](backend/README.md) for installation and commands.

AUTH-01 adds anonymous/customer sessions, browser-bound email challenges, atomic verification and token rotation, logout, persistent rate limits, and HTTPS cookie/CSRF protection. The suite passes 367 tests including real Neon integration checks. Clean non-editable installation passes 207 offline tests, with 160 database tests intentionally skipped in that mode. Email challenge delivery is controlled in tests; the normal API returns 503 until a real sender is configured and verified in MAIL-05. See the [authentication guide](docs/backend/authentication.md).

AUTH-03 adds local password login for provisioned staff, live organization/queue membership checks, credential rotation, and bounded Argon2id workers. Customer verification cannot create staff sessions. This adapter does not implement SSO or MFA. See the [staff authentication guide](docs/backend/staff_authentication.md).

AUTH-04 centralizes request identity and object authorization for tenant, owner, organization, and queue scopes. The identity endpoints use the shared service; protected object behavior is verified with fixture projections and real Neon identities. Case/source/state enforcement follows each feature.

SRC-01 provides an independent read-only reference API with SQLite and fictional parking/lounge bookings, transactions, historical policies, and explicit resource grants. Its 96 tests pass, including normal TCP requests and the signed local demo client. The assistant connector follows in SRC-02.

The worker remains a placeholder. Conversation/draft adoption, AI, and case workflows follow the task plan.

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
