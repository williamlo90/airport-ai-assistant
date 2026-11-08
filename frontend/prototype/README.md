# Airport AI Assistant design preview

[Project overview](../../README.md) | [Product tour](../../guides/PRODUCT_TOUR.md)

A static HTML, CSS, and JavaScript preview of the Westbridge Airport customer support journey.
It includes the homepage, guided conversation, contact verification, request review,
submission receipt, and case follow-up screens.

## Run locally

From the repository root:

```powershell
python -m http.server 4173 --bind 127.0.0.1 --directory frontend/prototype
```

Open [the homepage](http://127.0.0.1:4173). Stop with Ctrl+C.
The [product tour](../../guides/PRODUCT_TOUR.md) links to each customer screen and explains
what to inspect. No frontend installation, environment file, database, or provider key is needed.

## Interaction design

- Search guides and explore information, booking, transaction, or contact categories.
- Switch between English and Indonesian while preserving original customer text.
- Follow guided clarification and inspect the context carried into a staff-support request.
- Review verification and submission states before confirming a sample request.
- Inspect uncertain responses, retry recovery, case history, and same-issue follow-up.
- Use the available preview controls to explore alternative local states.

Use the preview controls to explore conversation, review, and case states. The React
application has its own [setup guide](../README.md).

## Asset provenance

The terminal artwork, SVG logo, and icons were created for the project.

Tuffy is served locally. Its bundled [author's license statement](assets/fonts/LICENSE.txt)
records the font's public-domain dedication and fallback permission. The original font source
is the [author's website](https://tulrich.com/fonts/).

Private development notes and raw visual-review artifacts remain local.
