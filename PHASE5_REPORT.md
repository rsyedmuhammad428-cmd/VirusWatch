# Phase 5 — RAG + Gemini — completion report

Date: 2026-11-01 · Scope: `Prompt.txt` §7 (schema), §10 (RAG ingestion), §11 (Gemini service), §12
(AI workflow), and `promprt2.txt` (LangChain + LangGraph integration) · Status: **implemented and
tested with fakes/real PostgreSQL; never run against a live Gemini API, live pgvector database, or
the public internet from inside application code**

This report follows the 8-point structure required after every phase (`Prompt.txt` §23).

## 0. What was and was not done

| Done this phase | Not done this phase |
|---|---|
| `app/rag/`: document/chunk models, HTML extraction (boilerplate stripping), PDF extraction (page-aware, via `pdftotext -layout`), paragraph-aware chunking with overlap, an explicit 5-source document catalog, a download→extract→chunk→embed→store pipeline, a SQL repository, a CLI command | Any real HTTP download (`httpx` not installed here) or a real call to `match_document_chunks()` against a pgvector-enabled database (same sandbox limitation as Phases 1-4) |
| `app/ai/`: Gemini LLM + embedding provider abstractions, the 8-step workflow (A-H) as plain tested functions, citation verification, prompt construction, a LangChain-compatible `Embeddings`/retriever adapter, a LangGraph `StateGraph` wrapper, service wiring that reuses `app.surveillance.queries.OBSERVATIONS` and `SqlRagRepository` | Any real call to the Gemini API (`google-genai` not installed, no API key) — the tested, exercised path is the graceful "Gemini not configured → insufficient-evidence answer" fallback |
| `POST /api/v1/ai/ask` route, wired into `app/api/v1/__init__.py` | Running the FastAPI app itself (`fastapi` not installed here, same as every previous phase) |
| 205 new tests (`tests/rag/`, `tests/ai/`, `tests/db/test_rag_sql.py`), all passing; `app.rag` added to the static SQL-contract scanner | Ingesting the live WHO/CDC/NIH/PubMed pages through the real pipeline — only their already-known facts were used to build fixtures (see §2) |
| `requirements.txt`/`pyproject.toml` updated with `google-genai`, `langchain-core`, `langgraph` (lower bounds, unverified — PyPI unreachable here) | Confirming current Gemini model identifiers against live API docs (`gemini_model`/`gemini_embedding_model` are left blank in `Settings`; see §4) |

## 1. Files created or modified

**New — `app/rag/` (RAG pipeline):**
- `models.py` — `DocumentMetadata`, `RawDocument`, `ExtractedPage`/`ExtractedDocument`, `ChunkingConfig`, `TextChunk`, `EmbeddedChunk`, `DocumentUpsert`, `IngestOutcome`, `SourceSpec`, `SourceKind` — mirror the `documents`/`document_chunks` CHECK constraints in Python.
- `catalog.py` — the explicit 5-source document catalog (§2 below).
- `extract.py` — `extract_html()` (strips `<nav>`/`<header>`/`<script>`/`<style>`/boilerplate, keeps heading structure) and `extract_pdf()` (shells out to `pdftotext -layout` per page, so every chunk can carry a page number).
- `chunking.py` — `chunk_document()`/`chunk_page_text()`: paragraph-boundary-aware splitting with configurable target size and overlap; never splits mid-paragraph unless a single paragraph exceeds the target.
- `downloader.py` — `HttpxDownloader` (import-guarded; never executed here).
- `ports.py` — `Downloader`, `Extractor`, `Embedder`, `RagRepository` Protocols.
- `pipeline.py` — `ingest_source()`/`ingest_sources()`: checksum-based skip-if-unchanged, independent per-source failure handling, `review_status="pending"` on every new document (a human must approve before the AI layer can retrieve it — enforced by RLS on `match_document_chunks()`, not just convention).
- `sql.py` / `sql_repository.py` — parameterized SQL for upsert/supersede/retrieve, covered by the existing static SQL-contract test (`tests/test_sql_contract.py`, `PACKAGES` now includes `"app.rag"`).

**New — `app/ai/` (Gemini + workflow):**
- `models.py` — `QuestionRequest`, `QuestionIntent`, `StructuredObservation`, `DocumentEvidence`, `EvidenceBundle`, `Citation`, `CitationCheckResult`, `WorkflowAnswer`.
- `ports.py` — `StructuredDataSource`, `DocumentRetriever`, `EmbeddingProvider`, `LLMProvider`, `GenerationRequest`/`GenerationResult` Protocols.
- `gemini.py` — `GeminiLLMProvider`, `GeminiEmbeddingProvider` (import-guarded on `google-genai`; model identifiers are read from `Settings`, never hardcoded).
- `prompts.py` — the system prompt encoding all 9 rules from §11/§12, plus the evidence-block renderers.
- `workflow.py` — steps A-H: `classify_intent` (A/B), `build_evidence_bundle` (C/D; step E is a deliberate no-op — no new arithmetic is ever introduced here), `_generate_with_bounded_retries` (F, max 2 attempts), `check_citations` via `app.ai.citation` (G), `answer_question` assembling the final `WorkflowAnswer` (H).
- `citation.py` — `check_citations()`: flags any `[S#]`/`[D#]` marker the model cited that wasn't in the supplied evidence, and flags a numeric-looking claim made with no marker at all.
- `service.py` — `AskService`, wiring real adapters: structured data via the **existing** `app.surveillance.queries.OBSERVATIONS` query (no new SQL), documents via `SqlRagRepository.match_chunks`, and a graceful null-provider fallback when Gemini is unconfigured.
- `langchain_adapter.py` — `VirusWatchEmbeddings`, a retriever adapter (import-guarded on `langchain-core`).
- `langgraph_adapter.py` — a typed `StateGraph` (`GraphState`) whose nodes call straight into `workflow.py`'s already-tested functions, so there's one implementation of the logic, not two (import-guarded on `langgraph`).

**New — API, CLI, tests, docs:**
- `app/api/v1/ai.py` — `POST /api/v1/ai/ask`, run as role `anon` (`public_conn`), per RLS.
- `app/api/v1/__init__.py` — registers the `ai` router.
- `app/cli.py` — new `ingest-documents` subcommand and `_ingest_documents()` (plus `EXIT_USAGE` was already present from Phase-5-adjacent notification work).
- `tests/rag/` (catalog, extract, chunking, models, pipeline + fakes), `tests/ai/` (workflow, service, gemini, citation, prompts, models, langchain_adapter, langgraph_adapter + fakes/conftest), `tests/db/test_rag_sql.py` (real PostgreSQL 16).
- `tests/fixtures/rag/nih_bulletin_week41_2026.pdf` — the first 3 pages of the real NIH bulletin PDF already on disk from Phase 1-4/data-expansion work, trimmed only for fixture size.
- `requirements.txt`, `pyproject.toml` — added `google-genai>=0.3`, `langchain-core>=0.3`, `langgraph>=0.2` (lint/format fixes applied to 2 pre-existing files this turn, see §6).
- `README.md` — Status table and Roadmap updated.
- This file.

## 2. Document catalog and the "what's real" honesty note

Per §10 step 1 ("source discovery or explicit URL ingestion... never a generic homepage"), the
catalog (`app/rag/catalog.py`) has 5 explicit, named document URLs:

| Slug | URL | What's actually real |
|---|---|---|
| `who_dengue_fact_sheet` | emro.who.int dengue page | Title, section structure ("regional situation", "prevention"), and the "2025 copyright, no publication date" fact were returned by a live WebSearch + WebFetch on 2026-11-01. |
| `who_outbreak_guidance` | who.int/health-topics/disease-outbreaks | Listed per spec; **not fetched this phase** (see §7 limitations). |
| `nih_pk_weekly_bulletin_w41_2026` | nih.org.pk/phb/weekly-bulletin | The actual bulletin PDF (not the index page) was supplied as a file attachment earlier in this engagement; its real text and page layout are in `tests/fixtures/rag/nih_bulletin_week41_2026.pdf`. |
| `cdc_respiratory_virus_guidance` | cdc.gov respiratory-virus-guidance page | Section headings and content summary were returned by a live WebSearch + WebFetch on 2026-11-01. |
| `pmc_dengue_karachi_2024` | pmc.ncbi.nlm.nih.gov PMC10902108 | Title, authors, journal, DOI (10.3389/ijph.2024.1606753), dates, and abstract figures (189 patients, 84.7% fever, etc.) were returned by a live WebFetch on 2026-11-01 and are copied verbatim into the catalog's `notes`. |

**Important limitation, stated plainly:** the WebFetch tool returns a *summarized* rendering of a
live page, not its verbatim HTML or text. That means there is no byte-for-byte "real" HTML fixture
for the WHO/CDC pages to extract from. `tests/rag/test_extract.py` instead runs `extract_html()`
against **synthetic HTML that quotes the real facts actually returned** (the real title, the real
section headings, the real "no publication date" observation) wrapped in ordinary
nav/header/script boilerplate written to test the stripping logic — the markup is synthetic, the
facts embedded in it are not. This is documented in the file's own header comment and in
`app/rag/catalog.py`, and is the one place in Phase 5 where "real" means "real facts, synthetic
markup" rather than "a real downloaded file," unlike the NIH PDF fixture, which is the genuine file.

## 3. Features implemented (mapped to spec)

- **§10 steps 1-9**: explicit catalog (1), `HttpxDownloader` (2, unexecuted), `extract_html`/`extract_pdf` (3-4), `chunk_document` (5), `DocumentMetadata` carried onto every chunk (6), `Embedder` protocol + `GeminiEmbeddingProvider` (7), `RagRepository.upsert_document` (8, checksum-based skip-if-unchanged and `supersedes` wiring satisfy step 9's "updates and removal of superseded content" via the existing `review_status='superseded'`/`superseded_by` columns).
- **§10 "each chunk must preserve..."**: all of title/publisher/source_url/published_at/retrieved_at/disease/country/document_type/language/review_status are on `DocumentMetadata` and copied into chunk metadata at persist time; page_number is preserved for PDF chunks specifically (promprt2.txt's "preserving... page numbers").
- **§11 rules 1-12**: enforced structurally, not just by prompt wording — rule 1 (numbers from deterministic data) by construction (the LLM never touches `StructuredDataSource`, only renders text describing it); rule 2 (every claim linked to evidence) by `check_citations`; rule 9 (no fake citations) by `check_citations`' "unknown marker" check; rule 10 (untrusted retrieved content) by system-prompt rule 4; rule 12 (timeouts/graceful failure) by `GenerationRequest.timeout_seconds` and `AskService`'s null-provider fallback.
- **§12 steps A-H**: implemented as the plain functions in `workflow.py`, described file-by-file in §1. LangGraph (promprt2.txt) wraps these same functions rather than re-implementing them (`langgraph_adapter.py`).
- **promprt2.txt LangChain/LangGraph**: `VirusWatchEmbeddings`/retriever adapter and the `StateGraph` wrapper exist, are structurally tested, and are import-guarded — see §7 for exactly what "tested" means without the packages installed.
- **CLI**: `python -m app.cli ingest-documents --source who|nih|cdc|pubmed|all [--dry-run]`, implemented in `app/cli.py`, only works once a real `Database`/downloader/Gemini embedder are configured (per §10's "must work only after actual source discovery and configuration are implemented... do not pretend the example command is already functional").

## 4. Commands to run

```bash
# Tests (as before, plus the new ones):
export TEST_PG_ADMIN_CONN="host=/path/to/socket/dir port=5432 user=postgres dbname=postgres"
NO_PGVECTOR=1 python -m pytest -q
ruff check . && ruff format --check .

# Once dependencies are installed and configured:
python -m app.cli ingest-documents --source who --dry-run
python -m app.cli ingest-documents --source all
uvicorn app.main:create_app --factory --reload
# then: POST /api/v1/ai/ask  {"question": "...", "country_code": "PK", "disease": "dengue"}
```

## 5. Environment variables needed

Already present in `app/config.py` from earlier phases, now actually used:
`GEMINI_API_KEY`, `GEMINI_MODEL`, `GEMINI_EMBEDDING_MODEL`, `EMBEDDING_DIMENSIONS` (default 768,
must match the chosen embedding model). New this phase: `AI_RETRIEVAL_MATCH_COUNT` (default 6),
`AI_MAX_OUTPUT_TOKENS` (default 1024), `AI_REQUEST_TIMEOUT_SECONDS` (default 20),
`RAG_CHUNK_TARGET_CHARS` (default 1200), `RAG_CHUNK_OVERLAP_CHARS` (default 150).

**`GEMINI_MODEL`/`GEMINI_EMBEDDING_MODEL` are left blank** — per §11 ("Do not hardcode an assumed
model identifier if availability has not been verified"), no identifier was confirmed against
current Gemini API documentation this phase. You will need to set these from Google's current
model list before `AskService` can do anything beyond the insufficient-evidence fallback.

## 6. Database migrations applied or pending

No new migration was needed: the `documents`/`document_chunks`/`match_document_chunks()` schema
already existed from migration `20261029000003`/`20261029000006` (Phase 2-era work, confirmed
against §7's field list — see summary of prior phases). `app/rag/sql.py` only reads/writes those
existing tables. `tests/db/test_rag_sql.py` runs 26 tests against real PostgreSQL 16 (via
`NO_PGVECTOR=1`, which strips the vector column/index/function the same way it has since Phase 2 —
`match_document_chunks()` itself is therefore still untested locally; CI, which uses a pgvector
image, covers it, same limitation as every previous phase).

## 7. Tests run and their actual outcomes

```
NO_PGVECTOR=1 TEST_PG_ADMIN_CONN=... python -m pytest -q
  --ignore=tests/test_auth.py --ignore=tests/test_health_and_errors.py --ignore=tests/test_config.py
→ 774 passed, 4 skipped
ruff check .          → All checks passed!
ruff format --check . → 157 files already formatted
```

Of the 774: 205 are new this phase (`tests/rag/` + `tests/ai/` + `tests/db/test_rag_sql.py`), the
rest are the unchanged Phase 1-4/data-expansion suite (re-run to confirm no regression). The 3
excluded files/modules are a pre-existing, unchanged limitation (`fastapi`, `structlog`,
`pydantic-settings` are not installable here — same as every previous phase; `test_config.py`'s
exclusion was newly confirmed this session when `pydantic-settings` was found missing again after
an environment reset, not something Phase 5 broke). The 4 skips are the pgvector-specific cases
`NO_PGVECTOR=1` strips.

**What "tested" does and does not mean here, concretely:**
- `extract_html`/`extract_pdf`/`chunk_document`: tested against real PDF bytes (NIH bulletin
  fixture) and the synthetic-markup/real-facts HTML described in §2. Never run on a live
  HTTP response.
- `workflow.answer_question`, `citation.check_citations`, `service.AskService`: tested with fakes
  standing in for the database connection, `SqlRagRepository`, and Gemini. The one real-Postgres
  exercise is `tests/db/test_rag_sql.py`, which checks the SQL (constraints, upsert, supersede,
  checksum skip-if-unchanged) — not `match_document_chunks()` itself (see §6).
- `GeminiLLMProvider`/`GeminiEmbeddingProvider`: the import-guard, timeout wiring and error
  wrapping are tested; the actual Gemini API has never been called.
- `langchain_adapter`/`langgraph_adapter`: the plain-Python logic (calling the wrapped
  methods/nodes directly) is tested; the actual `langchain-core`/`langgraph` base-class
  inheritance and the compiled `StateGraph` execution have never run, because the packages are
  not installed here (confirmed again this session: `pip install` fails with a DNS resolution
  error — the same no-PyPI-access constraint as every dependency in every phase so far).
- `POST /api/v1/ai/ask`: never run (no FastAPI). A static AST test is not yet written for this
  router specifically (`tests/test_router_sql_contract.py`'s `ROUTERS` list still only covers
  `surveillance.py`/`sources.py`) — `ai.py` doesn't call `conn.execute` directly (it delegates to
  `SqlRagRepository`), so the existing contract test's approach doesn't directly apply to it; this
  is a gap worth closing in a later pass, not a claim of coverage that doesn't exist.

## 8. Manual steps requiring your account or credentials

- A Gemini API key, and the current model identifiers for `GEMINI_MODEL`/`GEMINI_EMBEDDING_MODEL`
  confirmed from Google's own current documentation (deliberately left unset here).
- Installing `google-genai`, `langchain-core`, `langgraph`, and the rest of `requirements.txt` on
  a machine with PyPI access, then re-running the full suite there (this sandbox cannot).
- A Supabase project with `pgvector` enabled and the Phase 2 migrations applied, to exercise
  `match_document_chunks()` for real.
- Reviewing and approving ingested documents (`review_status: pending → approved`) before
  `/api/v1/ai/ask` can retrieve them — this is an intentional human gate, not a bug.
- Verifying licence/terms of use for each catalog source before production ingestion (not done
  this phase, consistent with the audit report's standing "licences unverified" note).

## 9. Known limitations and next phase

- `who_outbreak_guidance` is listed but not yet fetched/fixture-backed (§2).
- No real network call has been made from `app/rag`/`app/ai` code; the pipeline's correctness is
  proven with fakes, not a live run.
- `match_document_chunks()` and any vector-specific behavior remain CI-only, not locally verified,
  same as since Phase 2.
- `POST /api/v1/ai/ask` has no router-level static contract test yet (see §7).
- Per `Prompt.txt`'s roadmap, Phase 6 (Resend digests: scheduling/sending already built in the
  data-expansion work) and Phase 7 (live NIH/CDC connectors) remain the next priorities; Phase 5's
  `app/rag` pipeline can reuse the same connector/runner shape once Phase 7 adds live HTTP.
