# VirusWatch – dataset inspection & integration report

Date: 2026-10-31 · Scope: Phases 1–4 vs the supplied datasets · Status: **audit only, no code or data changed**

## 0. What was and was not done

| Done this turn | Not done this turn |
|---|---|
| Listed and extracted every ZIP into separate directories; SHA-256 of every dataset file (`docs/audit/dataset_sha256.txt`). Originals in the upload folder were not modified. | Loaded the real data into Postgres, or ran the analysis engine / API on it. |
| Ran the **real WHO FluNet `VIW_FNT.csv`** through the Phase 3/4 parser (`docs/audit/parse_real.py`, `pk_cov.py`). | Ran Phase 1 tests (FastAPI is not installable in this sandbox). Routers/`Database`/httpx fetcher remain never-executed. |
| Extracted text from all 12 NIH/provincial PDFs and read the national, Sindh/Karachi and respiratory tables of week 41. | Built any PDF parser. Table values below were read by eye from one report, not extracted by code. |
| Re-ran the pure test-suite on the unzipped Phase 4 package: **387 passed, 1 skipped** (DB and Phase-1 tests excluded). | Verified licences/terms of any source. |

## 1. Inventory

**Project ZIPs** – `viruswatch.zip` (Phases 1–2, 40 files), `viruswatch_phase3.zip`, `viruswatch_phase4.zip` are cumulative snapshots; Phase 4 is the one to use. (Not diffed file-by-file.)

**Dataset ZIPs**

| ZIP | Contents | Notes |
|---|---|---|
| `02_WHO_FluNet` | `VIW_FNT.csv` (32.3 MB, 190,020 data rows, 53 columns), `VIW_FLU_METADATA.csv` | Real data. |
| `1_Pakistan_Surveillance` | 12 PDFs (see §3) | All have a text layer (no OCR needed). |
| `03_US_CDC_International_Comparison` | NREVSS clinical, NREVSS public-health, ILINet, `CDC_Wastewater_Data_for_Measles_20261029.csv` (80,353 rows), `NWSSNationalSiteMap.csv` (1,255 rows) | |
| `research_paper` | 5 journal PDFs | Literature, not surveillance data (§5). |

**Duplicates**
- `IDSRS Weekly Report-41-2026-NA.pdf` and `…-NA (1).pdf` are byte-identical (same SHA-256). Keep one.
- `VIW_FLU_METADATA` ×3 (two loose uploads + inside the FluNet ZIP) and the three CDC CSVs uploaded loose are byte-identical to the copies in the ZIPs.

## 2. WHO FluNet – tested on the real file

- Header (53 columns) matches the metadata CSV and my `viw_fnt_fields.csv` fixture **exactly**; no missing or extra fields. UTF-8 BOM is handled.
- Parser, 8 supported countries: 190,020 rows read, 8,933 in scope, **0 rejected**, 0 duplicates. Pakistan: 952 rows → 7,426 observations, 0 rejections; USA: 1,983 rows.
- Pakistan data runs 2007 → ISO week of 2026-10-18 (latest sentinel week 2026-10-12 positivity).
- **Usable Pakistan positivity series is thin and stream-dependent:**

| Stream | Weeks with positivity | Range |
|---|---|---|
| sentinel | 261 | 2019-12-30 → 2026-10-18 (2020: 17, 2021: 45, 2022: 52, 2023: 36, 2024: 41, 2025: 42, 2026: 27) |
| nonsentinel | 12 | 2026 only |
| undefined | 67 (528 missing) | 2016–2019 only |

  Consequences: only `sentinel` supports baselines; 2020 is sparse (COVID); the seasonal baseline (≤5 prior years) has ~4–5 usable years; for Pakistan the "undefined" stream is mostly unusable (INF_NEGATIVE absent). Quality flags raised on PK: 612 `input_missing`, 4 `small_denominator`, 45 `denominator_differs_from_specimens_processed`.
- Data freshness at audit date: latest PK sentinel week is ~19 days old, inside the 28-day stale limit.
- Not verified: that the live OData URL in the connector returns this same file; the licence.

**Verdict: parser/schema integration with the real FluNet file is sound.** The remaining FluNet risk is statistical (series length), not format.

## 3. Pakistan NIH / provincial PDFs

| File | Content |
|---|---|
| IDSRS Weekly Report 33, 34, 35, 36, 37, 39, 40, 41 (2026) | NIH IDSR national bulletin, 23–27 pp. **Week 38 missing; weeks 1–32 missing.** |
| Weekly_Report-44-2024 | Week 44 of 2024 (isolated; issuer not checked) |
| AJK Week 7 2025 | AJK provincial IDSR bulletin (different layout; its own inner title still says "Week 03") |
| WINTER 61st Issue (SAAL) | Winter-season issue, Oct 2024–Feb 2025 – narrative, not weekly data |

Findings (from week 41; other weeks not table-by-table checked):
1. **Tables are machine-extractable** with `pdftotext -layout` (national by-province table, Sindh district table, respiratory sentinel table) but layout-fragile: wrapped names (`Karachi` / `Keamari` on two lines), differing spellings (`Karachi Keamari` vs `Karachi-Kemari`), page-split tables.
2. **Punjab is "NR" (not reported) in the national table; the "Total" column therefore excludes Punjab.** It is not a national figure and must never be labelled national.
3. **Karachi can be kept separate from Sindh**: the Sindh district table lists Karachi Central/East/Keamari/Korangi/Malir/South/West. But that table carries only the top-10 diseases (Malaria, AD, ILI, TB, ALRI, dog bite, VH, B. diarrhoea, typhoid, SARI) – **dengue and measles appear only at province level** (Sindh dengue 292, measles 40 in week 41). Karachi-level dengue/measles are not available from these bulletins; the set of columns may change weekly.
4. All counts are **suspected cases with no denominators or population**; they are not comparable with FluNet positivity or CDC rates.
5. Influenza/COVID/RSV lab results exist in the NIC sentinel table (ILI/SARI × tested/positive per site) – the only Pakistani lab-positivity source in these files for COVID-19.
6. No provenance manifest: no URL, publication date or licence for any PDF. File names are inconsistent (`NA`, `NG`, `(2)`, trailing `-`).

## 4. CDC / US comparison files

- **FluView CSVs** match `docs/source_notes_cdc_fluview.md`: title preamble, `X` for missing, MMWR weeks, starting 2023 week 40 (row counts not re-verified). NREVSS (% positive) and ILINet (% ILI) measure different things – keep separate.
- **Measles wastewater** (`mev_wt`): US only, 815 sites, 2024-12-09 → 2026-10-20. Problems for ingestion: `population_served` has thousands separators (`"140,000"`); two incompatible units (`copies/L wastewater` 46,111 rows vs `copies/g dry sludge` 34,242) – must never be pooled or compared; six `pcr_gene_target_agg` assay variants; 501 duplicate `sample_id`s (one sample, several assays); `flow_rate` blank in 43%; `date_updated` is `"2026 Oct 2…"` (non-ISO); detections are only 704 of 80,353 (0.9%); state codes are lowercase (`pa` = Pennsylvania, not Pakistan). Wastewater concentration is not a case count.
- `NWSSNationalSiteMap.csv` lists influenza A / RSV / SARS-CoV-2 monitoring sites, **not measles** – it does not map to the measles file's `site` ids.
- There are **no CDC measles case counts** and **no CDC COVID/dengue/malaria data** in the pack.

## 5. Research papers (RAG material, not observations)

Malaria in Sindh (Malaria Journal 2025); dengue clinical characteristics in Karachi 2019–23 (Sci Rep); multi-district dengue serotypes in Pakistan 2021–25 (PLOS ONE); two scoping reviews (digital early warning; ML for tropical disease). Store as `authority_level = research`, never as surveillance data; verify each licence before indexing text. Only two address Pakistan locally.

## 6. Integration gaps against Phases 1–4

| # | Problem | Severity |
|---|---|---|
| 1 | Only one connector exists (`who_flunet`). Seeds register NIH bulletin, CDC FluView and CDC NWSS as disabled sources, but none can be ingested; the measles wastewater file is a distinct NWSS dataset with no source row. | High (planned Phase 7) |
| 2 | No Sindh/Karachi (or any sub-national) geography rows; seed only has PK and US countries. Karachi district name variants need a canonical mapping. | High |
| 3 | Disease coverage: engine has an indicator rule for `influenza_positivity` only. Dengue, malaria, measles, COVID-19 have data in the pack (NIH PDFs, wastewater) but no indicator definitions, transmission classification or analysis rules. | High (prompt_3) |
| 4 | Schema supports counts and rates, but "suspected case count, no denominator, province missing (NR)" needs an explicit `reporting_completeness` / `data_status` convention so incomplete totals are not shown as national. | High |
| 5 | Wastewater units/assays must be part of the natural key or flagged, otherwise rows with different units collide or get compared. | Medium |
| 6 | Raw-data preservation: no `data/raw/` area or manifest in the project; checksums only exist in `docs/audit/`. | Medium |
| 7 | Source licences/terms (`terms_verified_at`) unverified for all sources; display-vs-export decision still pending. | Medium |
| 8 | Phase 1 app, routers, repositories and `Database` have still never been executed; DB tests were not re-run this turn. | Medium |
| 9 | Pack lacks: NIH weeks 1–32 and 38 (2026), earlier years, population denominators, WHO/other measles & COVID & dengue structured data, any Punjab-level data. | Data gap |

## 7. Recommended order before Phase 5

1. Add the raw-data area and provenance manifest (URL, publication date, retrieval date, licence, SHA-256) for all supplied files.
2. Seed geographies (Sindh, Karachi districts, other provinces) and the five-disease indicator catalogue with transmission classes.
3. Load the real FluNet CSV into a local Postgres and run analysis + API on it (turns §2 from "parses" into "works end-to-end").
4. Build the NIH bulletin extractor with golden tests from the supplied PDFs; treat Punjab-NR and non-national totals explicitly.
5. Then Phase 5 (RAG) with the five papers as `research` documents.

## 8. Decisions needed from you

- Missing NIH bulletins (2026 weeks 1–32, 38) and any WHO/other measles, COVID, dengue, malaria datasets – can you supply them, or should the first release cover only the weeks present?
- Licence stance: block public display of FluNet statistics until terms are verified, or display with attribution?
- Should NIH PDF tables be auto-extracted (needs per-week review) or hand-validated into CSV once for the first release?

## 9. Phase 7 addendum — new batch supplied 2026-11-04/06

Eight new records were added to `data/provenance/manifest.json` (now 42 total; checksums computed
from the files as supplied, never typed in — see `scripts/build_manifest_phase7_addendum.py`).
Each record's own `notes` field carries the full validation finding; summarized here:

| dataset_id | Data class | Licence | Headline finding |
|---|---|---|---|
| `kaggle_dengue_pakistan_2016_2020` | surveillance | CC BY-SA 4.0 (open) | Real, usable. ShareAlike: anything built from it inherits the same licence. |
| `opendengue_national_extract_v1_3` | surveillance | unverified | `case_definition_standardised` (Suspected/Confirmed/Total) and `T_res` (Week/Month/Year) both vary **within** the same country's rows — never pool across either without filtering to one value first. |
| `opendengue_emro_pakistan_filtered` | surveillance | unverified | Same source as above, Pakistan-only, extends it with 2023–2025 monthly rows. Overlapping years must not be summed with the national extract. |
| `malaria_atlas_project_pakistan_national_unit` | **health_context**, not surveillance | CC BY 3.0 (open) | These are MAP's *modelled* incidence/prevalence estimates, not reported case counts — classified health_context specifically so the analysis engine never treats them as a surveillance series. |
| `kp_idsr_district_week_compilation` | **compilation** | self-declared CC BY 4.0, **unverified** | See callout below — this one needs your decision before any further work touches it. |
| `tycho_pk_covid19_dataset_descriptor` | surveillance (metadata only) | CC BY 4.0 (verified, read from the file's own DataCite record) | This is the dataset's *citation record*, not the CSV. Downloading the real data needs a free Project Tycho account + API key, which this project does not have. |
| `tycho_api_reference` | compilation (docs) | none stated | Real, registration-gated REST API, documented for a future phase; no key available, so no connector was built against it. |
| `malaria_sindh_research_paper` | research | unverified | Cite/index only, per the existing research rule — never a case-count source. |

**Needs your decision: `kp_idsr_district_week_compilation`.** This is an unusually rich, carefully
documented panel — 59,824 district-week-condition rows for Khyber Pakhtunkhwa, with a reproducible
build script, a district-relabeling crosswalk, and a candid data-quality-issues list. But its own
README states the source table was "transcribed from each bulletin PDF using an AI-assisted
document-reading tool," asks readers to "cite the accompanying Data in Brief article," and names no
authors, journal, or DOI anywhere in the files supplied. A web search for that article by its
stated title found nothing — only the underlying NIH bulletin PDFs themselves (a source this
project already has its own independent extractor for, `app.datasets.nih_table`). This project's
rule against fabricating or unverifiably claiming integrations cuts the same way for a dataset
someone else fabricated or mis-cited: it is marked `compilation` / `unverified` (so the catalog's
own permission rules already restrict it to internal validation only), and **it has not been
ingested or persisted as observations.** If you can identify the actual authors or a working DOI
for the "Data in Brief article," or if this compilation is your own work and you can confirm that
directly, say so and it can be reclassified and integrated; otherwise it stays as reference
material only.

No connector was built this phase for any of these eight — Phase 7's connector work (CDC FluView,
NIH bulletin PDFs, CDC NWSS assessment) is reported separately in `docs/PHASE7_REPORT.md`. Building
ingestion for the dengue/malaria sources above (indicator definitions, a dengue/malaria section in
`app.analysis`, geography rows for sub-national KP districts) is sized like its own phase and is
not represented here as done.
