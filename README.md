# Airport AI Assistant

Bilingual airport support for passenger questions, booking assistance, and customer cases.

Find guidance, explain an issue, review a support request, and keep track of what happens next.
Airport AI Assistant brings that journey together with English and Indonesian interfaces,
authorized booking access, and structured case handling.

[Product tour](guides/PRODUCT_TOUR.md) · [Engineering](guides/CASE_STUDY.md) · [Run locally](guides/GETTING_STARTED.md)

![Airport AI Assistant homepage](guides/images/home.jpg)

## The Experience

- **Find help:** search guides or choose airport information, booking support, payment issues,
  or contact support.
- **Keep the context:** guided conversations retain the customer's report as the issue develops.
- **Review the request:** check the report, references, contact details, and destination before
  confirming submission.
- **Follow the case:** inspect status, handling results, and history; request further help on
  the same issue.
- **Switch languages:** move between English and Indonesian while keeping original messages
  as written.

<table>
  <tr>
    <td width="50%">
      <a href="guides/images/conversation.jpg"><img src="guides/images/conversation.jpg" alt="Booking support conversation"></a><br>
      <strong>Guided support</strong><br>
      Clarify the issue and find the next step.
    </td>
    <td width="50%">
      <a href="guides/images/case-details.jpg"><img src="guides/images/case-details.jpg" alt="Customer case details and handling result"></a><br>
      <strong>Case follow-up</strong><br>
      Keep the outcome and next action visible.
    </td>
  </tr>
</table>

[See the request review and complete screen tour →](guides/PRODUCT_TOUR.md)

## How It Works

```text
Help topic → Conversation → Contact verification → Request review
           → Confirmed case → Staff handling → Customer follow-up
```

The customer preview presents the screen flow. The backend handles identity, conversations,
request confirmation, and case transitions. A separate reference API supplies booking,
transaction, and policy records through authorized read access.

## Built With

| Layer         | Stack                                              |
| ------------- | -------------------------------------------------- |
| Frontend      | React, TypeScript, Vite                            |
| Backend       | Python, FastAPI, Pydantic                          |
| Database      | Neon PostgreSQL, Psycopg, Alembic                  |
| Reference API | FastAPI, SQLite, signed requests                   |
| Checks        | Pytest, Vitest, TypeScript, ESLint, GitHub Actions |

## Engineering

- **Modular services:** routes, domain rules, persistence, and integration adapters have clear
  responsibilities, checked by an executable dependency guard.
- **Scoped access:** tenant isolation, PostgreSQL row-level security, resource grants, and
  separate customer and staff authentication.
- **Explicit confirmation:** session-bound drafts and atomic case submission preserve consent,
  history, and audit records.
- **Reliable retries:** idempotency receipts and version checks protect against duplicate
  messages and case updates.
- **Source-aware responses:** booking access, payment details, and policy versions retain their
  own access rules and provenance.

[Read the engineering case study →](guides/CASE_STUDY.md)

## Try It

From the repository root:

```powershell
python -m http.server 4173 --bind 127.0.0.1 --directory frontend/prototype
```

Open [the customer preview](http://127.0.0.1:4173). Stop with Ctrl+C.

For the React application, backend, and reference service, use the
[setup guide](guides/GETTING_STARTED.md).

## Documentation

- [Product tour](guides/PRODUCT_TOUR.md)
- [Engineering case study](guides/CASE_STUDY.md)
- [Setup](guides/GETTING_STARTED.md) and [verification](guides/VERIFICATION.md)
- [Product contract](PRODUCT_CONTRACT.md)
- [Architecture](ARCHITECTURE.md)
- [Data and API contracts](DATA_AND_API_CONTRACTS.md)
- [Test plan](TEST_PLAN.md)

The [GitHub quality gate](.github/workflows/quality-gate.yml) checks documentation on pull
requests and manual dispatch. Application checks are available as a manual option.

## Source Use

Copyright (c) 2026 williamlo90. All rights reserved. See [LICENSE](LICENSE).
Third-party dependencies and assets retain their own terms.
