# Engineering case study

[Back to the project](../README.md) | [Product tour](PRODUCT_TOUR.md)

## The product problem

Airport support spans general guidance, parking and lounge bookings, transaction questions,
and issues that require a person to resolve. A useful assistant must keep customer context
intact while distinguishing information lookup from a confirmed support request.

Airport AI Assistant organizes this journey around an inspectable request and a durable case.
The customer can review what will be submitted, while staff can handle the resulting issue
within their authorized service scope.

## Separate product guidance from authority

The browser presents choices and captures customer intent. The backend resolves tenant and
identity context, checks access, and decides whether a state transition is allowed. A typed
message or a model-generated suggestion does not grant access to a booking or authorize a
case mutation.

The [identity services](../backend/src/airport_assistant/modules/identity/) own access rules.
The [composition root](../backend/src/airport_assistant/composition.py) wires services and
adapters, while the [dependency guard](../backend/tests/contracts/test_dependencies.py)
checks import boundaries. This makes the separation reviewable in source.

## Make customer confirmation a domain event

A request draft belongs to a session and has a canonical representation, version, expiry,
and explicit cancellation behavior. Confirmation commits the case together with consent,
history, audit, outbox data, and an idempotency receipt in a transaction.

This avoids treating a conversational response as proof of submission. Retries can refer to
the original command, and customer follow-up remains attached to the same issue rather than
silently creating a new report. See the [case services](../backend/src/airport_assistant/modules/cases/)
and [data contracts](../DATA_AND_API_CONTRACTS.md).

## Enforce isolation beyond the interface

The database uses tenant-aware relationships and forced row-level security. Runtime credentials
are separate from migration credentials. Request identity is scoped to a transaction so pooled
connections do not become a place to retain the previous caller's authority.

Customer verification is browser-bound and rotates sessions. Staff use a separate authentication
path and explicit organization and queue membership. UI visibility supplements these controls;
the server and database enforce access.

## Keep source ownership explicit

The [reference service](../reference_service/README.md) owns fictional booking, transaction,
policy, and resource-grant records. The application reads it through a signed API and validates
the returned projection. Booking access and payment-detail access are distinct grants.

Source observations and customer reports have different provenance. An unavailable source is
not reinterpreted as a failed payment, and a policy associated with a booking is not silently
replaced by a newer policy. These choices keep service explanations tied to their source.

## Build for inspection

The repository offers a static visual tour, an independently runnable React foundation,
backend domain tests, and a separately packaged reference API. Lockfiles support reproducible
installation. Sanitized errors and request identifiers support diagnosis without exposing raw
customer content or credentials.

The broader architecture includes AI orchestration, handoff, and email adapters. This case
study describes the checked-in identity, source, conversation, and case foundations; the
static tour demonstrates the intended customer experience with synthetic fixtures.
