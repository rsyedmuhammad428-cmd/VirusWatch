# AI Q&A API (Phase 5, enhanced Phase 8)

Base path `/api/v1`. The only endpoint, `POST /ai/ask`, runs as database role `anon`
(`public_conn`): the structured-data read goes through the same RLS the public `/surveillance`
endpoints use, and `match_document_chunks()` only ever returns `review_status = 'approved'`
documents (enforced inside the function itself, `SECURITY INVOKER`). Errors use the standard
envelope `{"error": {"code", "message", "request_id"}}`.

**Status: written but not yet run.** Same sandbox limitation as `docs/api_surveillance.md`
(FastAPI could not be installed here). The workflow logic (`app/ai/workflow.py`), citation
checking (`app/ai/citation.py`) and prompt rendering (`app/ai/prompts.py`) are fully unit
tested against fakes (`tests/ai/`); the route itself has never been started. A live answer also
needs a configured Gemini API key -- without one, every request gets the same safe
"insufficient evidence" response (see below) rather than an error.

| Endpoint | Auth | Purpose |
|---|---|---|
| `POST /ai/ask` | public | Evidence-grounded surveillance Q&A |

## Request

```json
{"question": "string, 1-2000 chars", "country_code": "PK (optional)", "disease": "dengue (optional)"}
```

## The A-H workflow (Prompt.txt §12)

1. **Classify intent** -- does the question want numbers, guidance, or both (`app.ai.workflow.classify_intent`).
2. **Gather structured evidence** -- real observations from `app.surveillance.service`'s read path (`[S#]` markers). Only runs for a numerical/both intent.
3. **Gather a "currently flagged" signal** (Phase 8) -- only when the caller did **not** name a `disease` and the question reads like "what's going around / how do I protect myself" (`app.ai.workflow._CURRENT_THREAT_HINTS`). Reuses `app.surveillance.service.build_alerts()` verbatim -- the same screening the public `/surveillance/alerts` endpoint returns -- so the disease discussed is never guessed by the model, only cited from a real, already-computed anomaly flag (`[T#]` marker).
4. **Gather document evidence** -- retrieved via `match_document_chunks()` (`[D#]` markers), re-scoped to the flagged disease from step 3 when one was found, so a question like "how do I protect myself?" actually retrieves that disease's real prevention document instead of an unscoped generic search. An explicitly-named `disease` in the request always wins and is never overridden.
5. **Generate** with Gemini, grounded only in the evidence gathered above.
6. **Check citations** -- every factual sentence in the draft must carry a `[S#]`/`[D#]`/`[T#]` marker that actually exists in the evidence bundle; a fabricated or missing marker triggers one bounded retry (`MAX_GENERATION_ATTEMPTS = 2`).
7. **Fall back honestly** -- if citations still don't check out, or no evidence was found at all, or Gemini isn't configured, the response is the same fixed "insufficient evidence" text (`app.ai.workflow.INSUFFICIENT_EVIDENCE_TEXT`) with `insufficient_evidence: true`, never a guess.

## The Phase 8 "intelligent agent" enhancement

Per the explicit requirement: when a user asks something like *"what's going around right now,
how do I protect myself?"* without naming a disease, the agent does not give generic "see a
doctor" advice. Instead:

- It names the actual disease currently flagged as `elevated`/`possible_anomaly` by the real
  analysis engine (`[T#]`), never one picked by the model.
- Its protective suggestions are specific to that disease and must be grounded in a retrieved,
  human-approved document (`[D#]`) -- see `app/rag/catalog.py` for dengue (WHO), malaria (CDC)
  and measles (CDC) prevention/guidance documents, plus a CDC respiratory-virus guidance
  document covering influenza and COVID-19.
- The system prompt (`app/ai/prompts.py` rules 10-11) explicitly forbids inventing a home
  remedy, dosage or supplement that isn't in the cited evidence, and requires the model to
  surface a documented danger if one exists -- e.g. it must not recommend aspirin/NSAIDs for a
  dengue-like fever, since that is a real, documented bleeding-risk danger, and the dengue
  source document covers safe fever management instead.
- The response's `limitations` array always discloses when the disease was auto-selected this
  way, so the caller knows to ask about a specific disease for a more targeted answer.

This is covered by `tests/ai/test_workflow.py` (signal gathering, re-scoping, never overriding
an explicit disease), `tests/ai/test_citation.py` (`[T#]` marker validation) and
`tests/ai/test_prompts.py` (the signal block and the new system-prompt rules), all against
fakes -- no live Gemini call has been made from this sandbox.

## Response shape

`AskResponse`: `answer`, `citations` (each with `marker`, `kind` -- `"structured"`, `"document"`
or `"signal"` -- `label`, `source_url`, `retrieved_at`), `reporting_periods`,
`last_update_timestamps`, `limitations`, `intent`, `insufficient_evidence`, `workflow_version`,
`model_identifier`.

## Samples

`docs/samples/ask.json` -- hand-built but schema-accurate (see the file's own `_note`): a
request/response pair showing the `[T#]`/`[D#]` signal-grounded answer shape, plus the
insufficient-evidence fallback shape.
