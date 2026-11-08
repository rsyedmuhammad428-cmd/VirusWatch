"""Append Phase 7's new dataset batch to data/provenance/manifest.json without touching the
existing entries (sha256 is computed from the files as staged, never typed in).

    python3 scripts/build_manifest_phase7_addendum.py --uploads DIR --extracted DIR

--uploads:   folder with the loose new uploads (CSV/JSON/PDF/txt)
--extracted: folder with the new ZIPs unpacked into dengue_pk/, district_week/, national_extract/
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.datasets.catalog import DataClass as C  # noqa: E402
from app.datasets.catalog import DatasetRecord  # noqa: E402
from app.datasets.catalog import LicenceState as L  # noqa: E402
from app.datasets.manifest import dump_manifest, load_manifest, sha256_file  # noqa: E402

TYCHO_CC_BY = "https://creativecommons.org/licenses/by/4.0/"
DENGUEDATASETS_PK = (
    "https://www.kaggle.com/datasets/aamir28/pakistan-dengue-surveillance-data-2016-2020"
)
OPENDENGUE_DATA_PAGE = "https://opendengue.org/data.html"
MAP_OPEN_ACCESS_POLICY = "https://malariaatlas.org/open-access-policy/"
MALARIA_JOURNAL_PAPER = (
    "https://malariajournal.biomedcentral.com/articles/10.1186/s12936-018-2500-5"
)


def spec():
    """(dataset_id, location, filename, kwargs). location is 'uploads' or 'extracted'."""
    rows = [
        (
            "kaggle_dengue_pakistan_2016_2020",
            "extracted",
            "dengue_pk/dengue_data.csv",
            dict(
                title="Pakistan Dengue Surveillance Data (2016-2020)",
                data_class=C.SURVEILLANCE,
                publisher=None,  # Kaggle uploader, not a named health authority
                download_url=DENGUEDATASETS_PK,
                licence_name="CC BY-SA 4.0",
                licence_state=L.OPEN,
                licence_evidence_url=(
                    "supplied licence.txt (denguedatasets package manifest), entry "
                    "'dengue_pakistan': License: CC BY-SA 4.0 -- not independently re-confirmed "
                    "against the live Kaggle page from this sandbox (no network egress to "
                    "kaggle.com)"
                ),
                coverage="420 monthly observations, Jan 2016 - Dec 2020",
                geography="province/territory (Punjab, Sindh, KP, Balochistan, ICT, AJK, GB)",
                indicators="Dengue_Cases (count), Dengue_Deaths (count)",
                notes="Secondary compilation (Kaggle uploader 'aamir28'), not the primary "
                "health-authority source. Licence taken from the supplied licence.txt, which "
                "itself documents a third-party package (denguedatasets, GPL-3.0) that bundles "
                "this file; the file's own per-dataset licence (CC BY-SA 4.0) governs the data, "
                "the package's GPL-3.0 governs only the bundling code and is not inherited by "
                "the CSV. ShareAlike requires any redistribution of this file (or data derived "
                "from it) to carry the same CC BY-SA 4.0 licence.",
            ),
        ),
        (
            "opendengue_national_extract_v1_3",
            "extracted",
            "national_extract/National_extract_V1_3.csv",
            dict(
                title="OpenDengue National-level case extract, V1.3",
                data_class=C.SURVEILLANCE,
                publisher="OpenDengue project (London School of Hygiene & Tropical Medicine)",
                download_url=OPENDENGUE_DATA_PAGE,
                licence_name=None,
                licence_state=L.UNVERIFIED,
                licence_evidence_url=OPENDENGUE_DATA_PAGE,
                coverage="29,873 country/period rows; weekly, monthly and yearly resolutions "
                "mixed (see S_res/T_res columns); includes Pakistan rows back to 1994",
                geography="country level (admin0) for most rows; some countries carry admin1",
                indicators="dengue_total (count), case_definition_standardised "
                "(Suspected/Confirmed/Total -- not uniform across rows, see notes)",
                notes="Published, peer-reviewed compilation (Scientific Data, 2024; "
                "https://doi.org/10.1038/s41597-024-03120-7 -- paper located by search, not "
                "independently re-read for its exact licence text from this sandbox). "
                "case_definition_standardised varies by row within the same country -- some "
                "Pakistan rows are 'Suspected and confirmed', others 'Confirmed', others "
                "'Total'; mixing these without filtering to one definition silently changes "
                "what is being measured. T_res also mixes Week/Month/Year within one series; "
                "resampling to a common period before use, and never comparing rows of "
                "different T_res directly, is required.",
            ),
        ),
        (
            "opendengue_emro_pakistan_filtered",
            "uploads",
            "filtered_data_EMRO_1793626824684.csv",
            dict(
                title="OpenDengue EMRO-region extract, pre-filtered to Pakistan",
                data_class=C.SURVEILLANCE,
                publisher="OpenDengue project (same source as opendengue_national_extract_v1_3)",
                download_url=OPENDENGUE_DATA_PAGE,
                licence_name=None,
                licence_state=L.UNVERIFIED,
                licence_evidence_url=OPENDENGUE_DATA_PAGE,
                coverage="2015-2025, a mix of annual rows (2015-2022) and monthly rows "
                "(2023 June - 2025 January)",
                geography="country level (Pakistan only in this extract)",
                indicators="dengue_total (count), case_definition_standardised",
                notes="Overlapping content with opendengue_national_extract_v1_3's Pakistan "
                "rows for the years both cover (2015-2022 annual figures match on inspection); "
                "this file additionally carries 2023-2025 monthly figures not present in the "
                "V1.3 national extract. Treat as a Pakistan-scoped update of the same source, "
                "not an independent second source -- do not sum or average the two files' "
                "overlapping years.",
            ),
        ),
        (
            "malaria_atlas_project_pakistan_national_unit",
            "uploads",
            "National_Unit-data.csv",
            dict(
                title="Malaria Atlas Project national-unit modelled estimates, Pakistan",
                data_class=C.HEALTH_CONTEXT,  # modelled estimates, not routine case reports
                publisher="Malaria Atlas Project (University of Oxford)",
                download_url=MAP_OPEN_ACCESS_POLICY,
                licence_name="CC BY 3.0 (maps); open-access policy states no restriction on "
                "publicly sourced data",
                licence_state=L.OPEN,
                licence_evidence_url=MAP_OPEN_ACCESS_POLICY,
                coverage="2000-2025, annual",
                geography="country level (admin0), Pakistan only in the supplied file",
                indicators="Incidence Rate (cases per thousand, modelled), Infection Prevalence "
                "(per 100 children, modelled)",
                notes="These are MODELLED ESTIMATES produced by MAP's statistical models (which "
                "blend survey data, routine reporting and covariates), not raw case counts or "
                "direct surveillance observations -- classified HEALTH_CONTEXT, not "
                "SURVEILLANCE, for that reason. Supplied without its generating script, but a "
                "copy of the 'malariaAtlas' R package source (the tool MAP publishes for "
                "programmatic access to this exact data) was supplied alongside it "
                "(malariaAtlas-master.zip), which is consistent with -- though does not prove "
                "-- that provenance. Must never be plotted or compared on the same axis as a "
                "case-count series (e.g. the NIH bulletin or OpenDengue data above) without "
                "clearly labelling one as modelled and the other as reported.",
            ),
        ),
        (
            "kp_idsr_district_week_compilation",
            "extracted",
            "district_week/idsr_kp_long.csv",
            dict(
                title="District-week notifiable infectious disease surveillance, Khyber "
                "Pakhtunkhwa, 2023-2026 (third-party compilation)",
                data_class=C.COMPILATION,
                publisher=None,
                download_url=None,
                licence_name="CC BY 4.0 (self-declared in the bundled README)",
                licence_state=L.UNVERIFIED,
                licence_evidence_url=None,
                coverage="2023 week 16, 2023 weeks 22-52, all 2024, all 2025, 2026 weeks 1-28 "
                "(164 of 169 weeks); 59,824 district-week-condition rows",
                geography="district / sub-division level within Khyber Pakhtunkhwa province",
                indicators="cases (count) for 16 notifiable conditions, no denominator",
                notes="IMPORTANT, read before using: the bundled README states this panel was "
                "'transcribed from each bulletin PDF using an AI-assisted document-reading "
                "tool' and asks users to 'cite the accompanying Data in Brief article', but "
                "names no authors, journal, or DOI anywhere in the supplied files, and a web "
                "search for the article by its stated title found no matching publication "
                "(only the underlying NIH bulletin PDFs themselves, which are a known, "
                "already-used source in this project). The CC BY 4.0 licence claim is "
                "therefore self-declared and UNVERIFIED, not confirmed against an independent "
                "record, and classified COMPILATION (a derivative re-processing of other "
                "documents) rather than SURVEILLANCE for that reason -- permissions() then "
                "correctly restricts it to internal validation only regardless of the licence "
                "claim. The README's own data-quality section is detailed and candid about "
                "known issues (condition right-censoring from 2023 week 32, a district label "
                "that changes meaning in 2024 week 50, 26 weeks whose columns do not reconcile "
                "to the printed total) -- these are the compiler's own disclosures, not "
                "independently verified by this project either. Recommendation: do not treat "
                "as a verified source or persist it as observations until you can confirm who "
                "produced it and whether the 'Data in Brief article' exists.",
            ),
        ),
        (
            "tycho_pk_covid19_dataset_descriptor",
            "uploads",
            "PK.840539006.json",
            dict(
                title="Project Tycho dataset descriptor: COVID-19 reported in Pakistan, "
                "2019-2021 (metadata only -- the data file itself was not supplied)",
                data_class=C.SURVEILLANCE,
                publisher="University of Pittsburgh (Project Tycho / MIDAS Coordination Center)",
                download_url="https://www.tycho.pitt.edu/dataset/PK.840539006",
                licence_name="CC BY 4.0",
                licence_state=L.OPEN,
                licence_evidence_url="https://creativecommons.org/licenses/by/4.0/",
                coverage="2019-12-30 to 2021-07-31 per the descriptor; actual row count unknown "
                "(this is the dataset's DataCite-style metadata record, not the CSV itself)",
                geography="country level, Pakistan",
                indicators="unspecified in the descriptor beyond 'case counts'; Project "
                "Tycho's own API (see tycho_api_reference below) exposes Fatalities, "
                "DiagnosisCertainty, PlaceOfAcquisition and other qualifiers per the API docs",
                notes="This file is the dataset's citation/metadata record (DataCite JSON), "
                "not the underlying CSV -- downloading the actual data requires a free Project "
                "Tycho account and an API key (see api.txt / tycho_api_reference), neither of "
                "which this sandbox has. Licence (CC BY 4.0) is read directly from this JSON's "
                "own 'licenses' field, so it is a verified first-party statement, not inferred.",
            ),
        ),
        (
            "tycho_api_reference",
            "uploads",
            "api.txt",
            dict(
                title="Project Tycho API reference documentation",
                data_class=C.COMPILATION,  # reference documentation, not a dataset
                publisher="University of Pittsburgh (Project Tycho)",
                download_url="https://www.tycho.pitt.edu/data/api",
                licence_name=None,
                licence_state=L.NONE_STATED,
                licence_evidence_url=None,
                coverage="n/a -- API documentation",
                geography="n/a",
                indicators="n/a",
                notes="Documents a real, registration-gated REST API (requires an account and "
                "an apikey query parameter) covering condition/country/pathogen/age-range "
                "lookups and a filtered query endpoint (ConditionName + location required; "
                "PeriodStartDate/PeriodEndDate range filters; max 20,000 rows per call, "
                "paginated via offset). No connector was built against it this phase: it needs "
                "a registered API key this project does not have, and no sample response was "
                "supplied to verify field names against. Kept as reference for a future phase.",
            ),
        ),
        (
            "malaria_sindh_research_paper",
            "uploads",
            "s12936-018-2500-5.pdf",
            dict(
                title="Malaria Journal article (research, Sindh malaria)",
                data_class=C.RESEARCH,
                publisher="BioMed Central / Malaria Journal",
                download_url=MALARIA_JOURNAL_PAPER,
                licence_name=None,
                licence_state=L.UNVERIFIED,
                licence_evidence_url=MALARIA_JOURNAL_PAPER,
                coverage="n/a -- research paper",
                geography="Sindh, Pakistan",
                indicators="n/a",
                notes="Research evidence only. Per app.datasets.catalog's own rule, research "
                "papers are never a source of current case counts; this is cited/indexed "
                "(RAG), never persisted as an observation.",
            ),
        ),
    ]
    return rows


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--uploads", type=Path, required=True)
    ap.add_argument("--extracted", type=Path, required=True)
    ap.add_argument(
        "--manifest",
        type=Path,
        default=Path(__file__).resolve().parents[1] / "data/provenance/manifest.json",
    )
    args = ap.parse_args()

    existing = load_manifest(args.manifest.read_text())
    existing_ids = {r.dataset_id for r in existing}

    new_records: list[DatasetRecord] = []
    for dataset_id, location, filename, kwargs in spec():
        if dataset_id in existing_ids:
            raise SystemExit(f"dataset_id already present in manifest: {dataset_id}")
        base = args.uploads if location == "uploads" else args.extracted
        path = base / filename
        if not path.is_file():
            raise SystemExit(f"file not found for {dataset_id}: {path}")
        new_records.append(
            DatasetRecord(
                dataset_id=dataset_id,
                filename=filename,
                sha256=sha256_file(path),
                size_bytes=path.stat().st_size,
                **kwargs,
            )
        )

    args.manifest.write_text(dump_manifest(existing + new_records))
    print(
        f"Added {len(new_records)} record(s); manifest now has {len(existing) + len(new_records)}."
    )


if __name__ == "__main__":
    main()
