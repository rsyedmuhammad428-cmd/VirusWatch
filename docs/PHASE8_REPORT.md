# Phase 8 report: intelligent agent + frontend integration

## 1. The "intelligent agent" enhancement (user request)

Ask: when a user asks something like "what's going around, how do I protect myself?", the agent
should name the currently most prevalent virus and give specific, authentic protection advice
(not generic text), including home-care measures.

Design: the agent identifies the disease from **real flagged data**, then answers from **cited
documents**. It never picks the disease or invents remedies itself.

| Piece | Change |
|---|---|
| `app/ai/models.py` | New `StructuredSignal`; `EvidenceBundle.signals`; `Citation.kind` gains `"signal"` |
| `app/ai/ports.py` | `StructuredDataSource.fetch_top_signals()` |
| `app/ai/service.py` | DB implementation reuses `surveillance.service.build_alerts()` verbatim (same screening as `/surveillance/alerts`, fresh data only) |
| `app/ai/workflow.py` | `gather_top_signal()` runs only when no disease was named and the question matches `_CURRENT_THREAT_HINTS`; document retrieval is re-scoped to the flagged disease; `[T#]` citations; limitation note disclosing the auto-selection |
| `app/ai/citation.py` | `[T#]` markers validated like `[S#]`/`[D#]` |
| `app/ai/prompts.py` | Signal block; rules 10-11: name the flagged disease, every concrete suggestion must carry a `[D#]`, never recommend an unsupported remedy, surface documented dangers (e.g. aspirin/NSAIDs in dengue-like illness) |
| `app/rag/catalog.py` | Added CDC malaria prevention and CDC measles vaccine guidance, so dengue, malaria, measles (own documents) and influenza/COVID-19 (CDC respiratory guidance) all have retrievable guidance |

Why grounded rather than free-form: the "hot water / gargling / home remedies" style of advice
is exactly where models repeat popular but unsupported or harmful claims. Home-care advice
therefore appears only if an approved document (WHO/CDC) says it. If a source says nothing about
a remedy, the agent says so instead of improvising. To widen the remedies the agent can give,
add more reviewed documents to the catalog (e.g. WHO/Pakistan NIH advisories); no code change
is needed.

Limitations (honest):
- Never exercised against a live Gemini call or live database in this sandbox; tested against fakes.
- Catalog documents still need human `review_status = approved` and ingestion before retrieval
  returns them.
- Only `elevated`/`possible_anomaly` series count as "trending", so with little ingested data
  the agent will have no signal and falls back to normal behaviour.
- `_CURRENT_THREAT_HINTS` is a keyword regex, English only.

## 2. Frontend integration deliverables

- `docs/api_ai.md`, `docs/api_me.md`, `docs/api_notifications.md` (with existing `api_surveillance.md`)
- `docs/samples/ask.json`, `me.json`, `notifications.json` (hand-built, schema-accurate, each with a `_note`; FastAPI has never run here)
- `docs/FRONTEND_INTEGRATION.md`: CORS, Supabase JWT flow, env vars, error envelope
- Noted gap: no `PUT /me` exists.

## 3. Tests

- `tests/ai/`: signal gathering, re-scoping, never overriding an explicit disease, `[T#]` citation and prompt coverage
- `tests/rag/test_catalog.py`: priority-disease coverage
- `tests/api/test_route_auth_matches_docs.py`: parses docs tables and router source (FastAPI can't be imported), fails on doc/code auth drift
- `tests/docs/test_samples.py`: sample JSON validity and provenance notes
- Whole suite: **872 passed, 4 skipped** (with `TEST_PG_ADMIN_CONN` and `NO_PGVECTOR=1`); `ruff check` clean. `test_auth.py`, `test_health_and_errors.py`, `test_config.py` excluded: they need `fastapi`/`structlog`/`pydantic-settings`, which cannot be installed here.

## 4. Not done
- Air Quality Index feature (awaiting your decision).
- KP district-week IDSR dataset not wired into ingestion.
- Phase 9 (evaluation, exports, deployment).
