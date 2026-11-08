# Product tour

[Back to the project](../README.md) | [Getting started](GETTING_STARTED.md)

## Open the design preview

From the repository root:

```powershell
python -m http.server 4173 --bind 127.0.0.1 --directory frontend/prototype
```

Open [Westbridge Airport](http://127.0.0.1:4173). The preview runs locally using synthetic
scenarios. It needs no API keys, database, or frontend package installation.

## Explore the customer experience

| Screen         | What to inspect                                                                                                        | Local preview                                                         |
| -------------- | ---------------------------------------------------------------------------------------------------------------------- | --------------------------------------------------------------------- |
| Home           | Help categories, searchable guides, FAQ, and English/Indonesian controls.                                              | [Home](http://127.0.0.1:4173/)                                        |
| Conversation   | Guided clarification, language changes with original text preserved, retry behavior, and context for contacting staff. | [Booking help](http://127.0.0.1:4173/conversation.html?topic=booking) |
| Verify contact | Email verification states and language-aware form guidance.                                                            | [Verification](http://127.0.0.1:4173/request.html?step=verify)        |
| Review request | Review the report and service context before explicit confirmation.                                                    | [Request review](http://127.0.0.1:4173/request.html?step=review)      |
| Submission     | Distinguish a submission receipt, case availability, and an uncertain response.                                        | [Submission](http://127.0.0.1:4173/submission.html)                   |
| Case details   | Inspect the recorded outcome and history, then try the same-issue follow-up flow.                                      | [Case details](http://127.0.0.1:4173/case.html?preview=closed)        |

Switch between English and Indonesian, compose a message, and inspect the available local
preview states. Labels follow the selected interface language; original customer text stays
as written. Stop the preview server with Ctrl+C when finished.

These pages are independent design fixtures. They do not authenticate customers, create real
cases, send email, or query a live airport. Scripted responses demonstrate interaction design.
The React application and backend have their own setup paths in [Getting started](GETTING_STARTED.md).

## Visual assets

Westbridge Airport is fictional. The terminal illustration was generated for this project.
Tuffy font files are served locally and retain their bundled
[author's license statement](../frontend/prototype/assets/fonts/LICENSE.txt).
