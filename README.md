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

## Implementation status

This repository currently contains the reviewed planning documents. Application code, executable setup, and deployment instructions will be added during implementation. Document review is separate from implementation testing.

The architecture uses a modular backend and Neon PostgreSQL, with a VPS/Kubernetes deployment direction. Model and email providers are not selected. Booking and transaction integrations initially use a separate reference service with test data.

## Local configuration

`.env.example` lists initial configuration placeholders. Copy it to `.env` when implementing the application and populate values locally. The template is not an executable setup and will evolve with the configuration schema.

Never commit `.env`, API keys, database credentials, private keys, real customer data, or unredacted provider payloads. Use synthetic fixtures for development and testing.
