# Embedding selection spike

Compare text retrieval independently of generated answers. The fixture contains
synthetic English/Indonesian airport information, not real-airport policies or
customer data. This harness does not implement production indexing or tenant ACLs.

From the repository root, with the existing backend environment:

```powershell
.\backend\.venv\Scripts\python.exe evaluations/runners/embedding_selection.py prepare
.\backend\.venv\Scripts\python.exe -m pytest evaluations/runners/test_embedding_selection.py -q
```

`prepare` validates the fixture and fixed schedule, computes the lexical baseline
and writes hashes/budget estimates to ignored `local/provider-selection/`. It does
not load `.env`, import API clients, contact providers or modify the database.
No new dependency installation is needed; live mode uses existing backend
`httpx` and `python-dotenv` dependencies.

Live mode is separate. After reviewing the frozen manifest, approving a run scope
and USD cap, put `EVAL_OPENAI_API_KEY` and `EVAL_GEMINI_API_KEY` in local `.env`.
Never commit keys. Filling keys does not run the harness or enable production AI.

```powershell
# Only after explicit authorization for this manifest and cap:
.\backend\.venv\Scripts\python.exe evaluations/runners/embedding_selection.py live --approved-manifest <hash-from-prepare> --approved-cap-usd <approved-cap>
```

The synchronous schedule has 40 planned HTTP requests, at most 10 retries globally,
one request in flight and a separate hard request ceiling of 120. Each retry has
its own cost reservation; unknown-charge attempts retain their reservation.
Timeout is 30 seconds; redirects, environment proxies and automatic retries are
disabled. A persistent 429 stops the run. Missing credentials or mismatched hashes
stop before any provider call. Do not rerun a live command without checking scope
and the existing ledger: there is no automatic resume or cross-run budget.

Cost estimates use 2,048 input tokens per item as a conservative reservation for
these short inputs (each at most 1,024 UTF-8 bytes). These are not exact tokenizer
counts or invoices. Actual billing/credits and current pricing need separate
verification before an authorized run. The configuration proposes $0.10; this is
not approval to spend it. The runner records reported token counts when available,
retains reservations when usage is unavailable, and stops if usage exceeds its
reservation. Application-side controls are not a provider-enforced billing cap.

Ranking uses a fixed target-language candidate pool per query. Entire intent
families share one split. Calibration selects the abstention threshold before
scoring holdout. Missing query/corpus vectors fail closed; unsuccessful requests
stay in end-to-end denominators. Successful-request quality is reported separately.
Hit@5, Recall@5 and MRR@5 are separate; the current single-relevant-chunk fixture
makes Hit@5 and Recall@5 equal and does not test multi-source completeness.
The 16 relevant holdout rows come from four intent families expressed across four
language directions; they are dependent samples, not 16 independent user intents.
This small selection fixture does not establish statistical significance.

Evidence includes sanitized per-attempt status/latency, hashes, vectors, per-query
rankings, per-direction metrics and the lexical baseline. Raw provider bodies,
headers, credentials and `.env` content are never written to reports. Latency is
per batch request, not per-user response or a production SLA. Synthetic/offline
checks prove harness behavior only. A complete live report still requires review;
the harness does not automatically declare a model winner.


# Agent model selection (AI-04)

`agent_selection.py` evaluates production planner payloads/parsers on34 EN/ID
cases from17 families, with2 repeat passes, against OpenAI/Gemini candidates and
a deterministic intake/rule baseline. It is a component benchmark with fixed
source context, not a live retrieval/RLS/transaction or UI usability benchmark.

```powershell
.\backend\.venv\Scripts\python.exe evaluations/runners/agent_selection.py prepare
.\backend\.venv\Scripts\python.exe -m pytest evaluations/runners/test_agent_selection.py evaluations/runners/test_agent_selection_qa.py -q
```

Prepare is offline and does not read credentials. Review the generated
`local/ai-04/prepared_manifest.json` before authorizing a live run. Proposed scope:
144 attempts maximum,0 automatic retries,one request in flight,20-minute run
admission bound,USD1.00 cap. The manifest prints the exact conservative reservation.
The earlier embedding authorization does not authorize this run.

```powershell
# Only after this exact manifest and cap have been explicitly authorized:
.\backend\.venv\Scripts\python.exe evaluations/runners/agent_selection.py live --approved-manifest <hash> --approved-cap-usd 1.00
```

Uses EVAL_OPENAI_API_KEY and EVAL_GEMINI_API_KEY locally; do not paste keys into
chat. Runtime model/email flags remain off. A one-shot claim prevents accidental
reruns; do not delete it to force a resume. Journal reservations precede network,
unknown usage retains reservation, version/usage guards also run on rejected
completions. No raw headers/credential/provider bodies are retained.

Gemini uses an evaluation-only official OpenAI-compatible bridge to the same
canonical prompt/schema. Parser-envelope normalization does not identify the
provider; genuine observed model IDs are recorded separately. A Gemini benchmark
win does not activate a production Gemini adapter. None-vs-minimal reasoning is
recorded explicitly.

Outputs require semantic review, including answers/qualifiers, summaries and
translations. Provider-label-hidden packets and random IDs do not establish a
double-blind human study. Record agent/human reviewer identity honestly, and use
the packet hash and all review IDs. Missing/altered grades/artifacts are rejected.

```powershell
.\backend\.venv\Scripts\python.exe evaluations/runners/agent_selection.py assess --run-folder local/ai-04/run-<id> --review-file local/ai-04/run-<id>/qa.review.json
```

Assessment produces explicit gates and diagnostics, never an automatic winner or
production activation. Independent family counts and per-language/task results
remain separate from dependent EN/ID repeat rows. Source gold positions are
counterbalanced; condition-sufficient alternatives and safe abstention are
pre-registered, with exact negative reason agreement reported separately. Rule
baseline translation is staff deferral, not free automatic translation. Actual
user clicks, staff labor and end-to-end workflow latency are not measured.

## AI-04 revalidation v2

Use `agent_selection_v2.py` for the new dataset/rubric and `local/ai-04-v2/` output namespace. `gemini_diagnostic.py prepare` freezes a separate 3-request calibration-only probe (proposed cap USD0.02); it does not authorize or launch the 144-request model comparison (proposed cap USD1.00). Both prepare commands are offline; live commands require separate exact manifest/cap authorization. Run probe first, review it, obtain main scope approval separately. Retain original dataset/run evidence; v1 exposed holdout is now diagnostic regression. New heldout families remain fixed before provider calls. v2-only planning baseline supports generic declared reference formats; remaining baseline limitations, human translation deferral and small paired-family scope must be reported. Protocol docs/backend/agent_revalidation_v2.md;41mocked tests validate harness only, not model quality.

## Active provider decision

OpenAI is the selected project provider by user decision on5November2026. Further Gemini probes and the prepared two-provider v2 comparison are retired from the active plan. Historical fixtures/manifests/results remain evidence, not a live run instruction. Next preparation must create a separately frozen OpenAI-only schedule/cost scope with the v2 rule baseline; no old manifest filtering/reused authorization. Model quality remains unqualified until revalidation. No paid execution is authorized by this decision.

## Active OpenAI-only quality validation

`agent_openai_validation.py prepare` freezes the selected OpenAI snapshot against unchanged v2 cases and the v2 rule baseline in `local/ai-04-openai/`. Scope72maxrequests (4preflight+68eval),0retry,serial,15minadmission; proposalcapUSD0.60/reservationUSD0.56509950. Live requires a separately approved exact manifest and cap; no Gemini credential/job/fallback. Report/assessment list only OpenAI and rule baseline as appropriate, preserving52holdout denominators. Mocked complete reviews test artifact integrity, not semantic quality. Actual quality remains pending execution and independent semantic review. Protocol docs/backend/agent_openai_validation.md. Historical two-provider manifests are not active run instructions.

## Active acceptance v3 (offline preparation)

`agent_acceptance_v3.py prepare` binds the frozen repaired prompt, new v3 data, and bounded evaluation-only retries in a new output namespace.72logical jobs/max80physical calls,8shared retries/max1perjob,transient-only/permanent-error precedence/circuit3/Retry-After limits. ProposalcapUSD0.75,conservative reserveUSD0.64803750 including8×worst-attempt reserve. Physical failure ledger and unknown costs remain; first-attempt and eventual metrics are separate. Prod adapter/business actions have no new retries. Live requires a new exact manifest/cap approval, not oldrun permission.88mocked tests prove harness behavior only. Protocol docs/backend/agent_acceptance_v3.md.

## Active translation-only retest preparation

`translation_acceptance.py prepare` freezes the repaired production prompt and a new 20-row bilingual translation dataset. Scope: 42 primary jobs, at most four shared retries (one/job), max46HTTP calls, proposed capUSD0.45 and conservative reservationUSD0.37459800. No baseline or other component is rescored;32holdout outcomes/16perdirection remain the denominators. Review40evaluation outputs (including8calibration);2preflights check contracts. Meaning invariants admit faithful alternative wording. First-attempt/eventual scores and all physical costs remain separate. New namespace and exact manifest approval are required before live; no paid execution is part of preparation. Protocol docs/backend/translation_acceptance.md;96offline tests validate mechanics only.
