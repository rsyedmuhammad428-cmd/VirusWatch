"""Build data/provenance/manifest.json from the files actually supplied (checksums are computed,
never typed). Facts that were not read from a source page or from the file itself are left as None
or marked unverified; nothing is guessed.

  python3 scripts/build_manifest.py --uploads DIR --extracted DIR > data/provenance/manifest.json

--uploads:   folder with the loose uploads (CSV/PDF)
--extracted: folder with the dataset ZIPs unpacked into pk/ flunet/ cdc/ papers/ merge1..3/
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.datasets.catalog import DataClass as C  # noqa: E402
from app.datasets.catalog import DatasetRecord  # noqa: E402
from app.datasets.catalog import LicenceState as L
from app.datasets.manifest import dump_manifest, sha256_file  # noqa: E402

WHO_COPYRIGHT = "https://www.who.int/about/who-we-are/publishing-policies/copyright"
OPENDATA_COVID = (
    "https://opendata.com.pk/dataset/covid-19-cases-in-pakistan-2020-2022/resource/"
    "7d37db2e-f9e3-4c48-8297-b49ae655b344"
)
OPENDATA_COVID_DL = (
    "https://opendata.com.pk/dataset/c1261e6a-4e8c-4514-8178-c0b460e7cdca/resource/"
    "7d37db2e-f9e3-4c48-8297-b49ae655b344/download/covid-19-cases-in-pakistan-2020.csv"
)
OPENDATA_SITES = "https://opendata.com.pk/dataset/pakistan-healthsites"
OPENDATA_SITES_DL = (
    "https://opendata.com.pk/dataset/6ce55035-6655-4095-b525-8aa18b0ee92c/resource/"
    "39532120-99d6-4e1c-a657-9ad6d480d212/download/pakistan.csv"
)
NIH = "National Institutes of Health, Islamabad (Center of Disease Control), per the document"
CDC = "U.S. Centers for Disease Control and Prevention (as named in the file set)"


def spec():
    """(dataset_id, location, filename, kwargs)."""
    rows = [
        (
            "who_flunet_viw_fnt",
            "extracted",
            "flunet/02_WHO_FluNet/VIW_FNT.csv",
            dict(
                title="WHO FluNet VIW_FNT (global weekly influenza virology)",
                data_class=C.SURVEILLANCE,
                publisher="World Health Organization (FluNet)",
                licence_state=L.UNVERIFIED,
                licence_evidence_url=WHO_COPYRIGHT,
                coverage="190,020 rows; Pakistan rows 2007 - 2026-10-18 (week start)",
                geography="country (ISO3)",
                indicators="influenza detections, negatives, specimens",
                notes="WHO page names CC BY-NC-SA 3.0 IGO for WHO publications and requires "
                "permission for commercial use; it does not state terms for FluNet data "
                "specifically. Treated as unverified.",
            ),
        ),
        (
            "cdc_nrevss_clinical",
            "extracted",
            "cdc/03_US_CDC_International_Comparison/ICL_NREVSS_Clinical_Labs.csv",
            dict(
                title="CDC FluView NREVSS clinical labs",
                data_class=C.SURVEILLANCE,
                publisher=CDC,
                coverage="MMWR weeks from 2023 week 40",
                geography="US national",
                indicators="% specimens positive for influenza (laboratory positivity)",
            ),
        ),
        (
            "cdc_nrevss_public_health",
            "extracted",
            "cdc/03_US_CDC_International_Comparison/ICL_NREVSS_Public_Health_Labs.csv",
            dict(
                title="CDC FluView NREVSS public health labs",
                data_class=C.SURVEILLANCE,
                publisher=CDC,
                coverage="MMWR weeks from 2023 week 40",
                geography="US national",
                indicators="% specimens positive for influenza (laboratory positivity)",
            ),
        ),
        (
            "cdc_ilinet",
            "extracted",
            "cdc/03_US_CDC_International_Comparison/ILINet.csv",
            dict(
                title="CDC ILINet outpatient ILI",
                data_class=C.SURVEILLANCE,
                publisher=CDC,
                coverage="MMWR weeks from 2023 week 40",
                geography="US national",
                indicators="% visits for influenza-like illness (syndromic; not comparable to "
                "laboratory positivity)",
            ),
        ),
        (
            "cdc_wastewater_measles",
            "extracted",
            "cdc/03_US_CDC_International_Comparison/CDC_Wastewater_Data_for_Measles_20261029.csv",
            dict(
                title="CDC wastewater measles virus (mev_wt)",
                data_class=C.SURVEILLANCE,
                publisher=CDC,
                coverage="samples 2024-12-09 - 2026-10-20",
                geography="US state / sewershed, 815 sites",
                indicators="measles RNA concentration; two incompatible units (copies/L, copies/g)",
            ),
        ),
        (
            "cdc_nwss_site_map",
            "extracted",
            "cdc/03_US_CDC_International_Comparison/NWSSNationalSiteMap.csv",
            dict(
                title="CDC NWSS site map",
                data_class=C.FACILITY_REFERENCE,
                publisher=CDC,
                coverage="week end 2026-10-17 (as listed)",
                geography="US county",
                indicators="monitored viruses per site (influenza A, RSV, SARS-CoV-2; not measles)",
            ),
        ),
        (
            "opendata_covid_pk_2020",
            "uploads",
            "covid-19-cases-in-pakistan-2020.csv",
            dict(
                title="COVID-19 cases in Pakistan 2020 (OpenData Pakistan)",
                data_class=C.SURVEILLANCE,
                publisher=None,
                download_url=OPENDATA_COVID_DL,
                licence_state=L.NONE_STATED,
                licence_name="No License Provided",
                licence_evidence_url=OPENDATA_COVID,
                coverage="2020-03-11 - 2020-06-12 with gaps (2-3 May, 8-10 Jun)",
                geography="province/area (ICT, Punjab, Sindh, KP, Balochistan, AJK, GB, KPTD)",
                indicators="cumulative confirmed, discharged, expired; other columns have no data "
                "dictionary and are not used",
                notes="Original publisher not named on the source page; no column descriptions.",
            ),
        ),
        (
            "who_gho_pakistan",
            "uploads",
            "health_indicators_pak.csv",
            dict(
                title="WHO Global Health Observatory export, Pakistan",
                data_class=C.HEALTH_CONTEXT,
                publisher="World Health Organization (GHO)",
                licence_state=L.UNVERIFIED,
                licence_evidence_url=WHO_COPYRIGHT,
                coverage="YEAR 1950 - 2030 as listed",
                geography="country",
                indicators="647 indicators; includes measles reported cases and "
                "malaria confirmed cases (annual)",
                notes="1,988 key groups are ambiguous (a disaggregating dimension was dropped) and "
                "must not be loaded; use the Numeric column only.",
            ),
        ),
        (
            "dhs_quickstats_pakistan",
            "uploads",
            "national-dhs-quickstats.csv",
            dict(
                title="DHS national quickstats, Pakistan",
                data_class=C.HEALTH_CONTEXT,
                publisher="The DHS Program (per DHS_CountryCode/SurveyId columns)",
                coverage="surveys 1991, 2006, 2012, 2017",
                geography="country",
                indicators="survey indicators (fertility, vaccination, mortality, ...)",
            ),
        ),
        (
            "pk_key_indicators_health",
            "uploads",
            "key-indicators-of-health-in-pakistan.csv",
            dict(
                title="Key indicators of health in Pakistan",
                data_class=C.HEALTH_CONTEXT,
                publisher=None,
                coverage="2013-14 and 2018-19 survey rounds",
                geography="national, province",
                indicators="immunisation, diarrhoea, infant mortality (percent / per 1,000)",
            ),
        ),
        (
            "pk_hiv_treatment_centres_2020",
            "uploads",
            "hiv-treatment-centres-in-pakistan-2020.csv",
            dict(
                title="HIV treatment centres in Pakistan 2020",
                data_class=C.HEALTH_CONTEXT,
                publisher=None,
                coverage="2020 (per file name)",
                geography="facility, province",
                indicators="people registered / on ART by sex and age group",
            ),
        ),
        (
            "pk_healthsites_pakistan_csv",
            "uploads",
            "pakistan.csv",
            dict(
                title="Pakistan healthsites (OpenData Pakistan)",
                data_class=C.FACILITY_REFERENCE,
                publisher="Gallup Pakistan (per source page; upstream HDX healthsites)",
                download_url=OPENDATA_SITES_DL,
                licence_state=L.NONE_STATED,
                licence_evidence_url=OPENDATA_SITES,
                coverage="edits 2008 - 2019",
                geography="point",
                indicators="facility locations; no disease data",
                notes="OpenStreetMap-derived columns (osm_id); upstream licence not verified.",
            ),
        ),
        (
            "pk_healthsites_pakistan_1_csv",
            "uploads",
            "pakistan_1.csv",
            dict(
                title="Pakistan healthsites, second file",
                data_class=C.FACILITY_REFERENCE,
                publisher=None,
                coverage="edits 2008 - 2020",
                geography="point",
                indicators="facility locations; no disease data",
                notes="Overlaps pakistan.csv on 3,099 osm_id values; not a distinct dataset.",
            ),
        ),
        (
            "nih_idsr_week26_2026",
            "uploads",
            "Public_Health_Bulletin_IDSRS_Weekly_Report-26-2026.pdf",
            dict(
                title="NIH IDSR weekly bulletin, week 26 2026",
                data_class=C.SURVEILLANCE,
                publisher=NIH,
                coverage="week 26, 2026",
                geography="province, Sindh districts",
                indicators="suspected case counts (no denominators)",
            ),
        ),
        (
            "nih_winter_issue_61",
            "extracted",
            "pk/1_Pakistan_Surveillance/WINTER 61st-Issue-SAAL.pdf",
            dict(
                title="Winter-season issue 61 (SAAL)",
                data_class=C.RESEARCH,
                publisher=None,
                coverage="Oct 2024 - Feb 2025",
                geography="national narrative",
                indicators="narrative; not a data table",
            ),
        ),
    ]
    for name in (
        "IDSRS Weekly Report-33-2026.pdf",
        "IDSRS Weekly Report-34-.pdf",
        "IDSRS Weekly Report-35-2026-NG.pdf",
        "IDSRS Weekly Report-40-2026-NG (2).pdf",
        "IDSRS Weekly Report-41-2026-NA (1).pdf",
        "IDSRS Weekly Report-41-2026-NA.pdf",
        "Weekly Report-36-2026.pdf",
        "Weekly Report-37-2026.pdf",
        "Weekly Report-39-2026.pdf",
        "Weekly_Report-44-2024.pdf",
        "AJK__Week_7_20250319121659.pdf",
    ):
        slug = "".join(ch if ch.isalnum() else "_" for ch in name.lower()).strip("_")
        rows.append(
            (
                f"nih_{slug}",
                "extracted",
                f"pk/1_Pakistan_Surveillance/{name}",
                dict(
                    title=name.removesuffix(".pdf"),
                    data_class=C.SURVEILLANCE,
                    publisher=NIH
                    if "AJK" not in name
                    else "AJK Directorate General Health (per document)",
                    coverage="weekly bulletin (see file name)",
                    geography="province / district",
                    indicators="suspected case counts (no denominators)",
                ),
            )
        )
    for i, part in enumerate(("1-400", "401-800", "801-813"), 1):
        rows.append(
            (
                f"merge_compilation_{part.replace('-', '_')}",
                "extracted",
                f"merge{i}/Merge dataset_compressed_pages_{part}.pdf",
                dict(
                    title=f"Merged compilation of bulletins, pages {part}",
                    data_class=C.COMPILATION,
                    publisher=None,
                    coverage="2026 weeks 1-32 and 38 (+ one AJK bulletin and guidance)",
                    geography="province / district",
                    indicators="re-bundled copies of NIH bulletins; prefer the originals",
                ),
            )
        )
    for name in (
        "12936_2025_Article_5505.pdf",
        "ijerph-22-01104.pdf",
        "journal.pone.0353487.pdf",
        "s12879-026-13993-1.pdf",
        "s41598-024-83425-0.pdf",
    ):
        rows.append(
            (
                f"paper_{name.removesuffix('.pdf').lower().replace('-', '_')}",
                "extracted",
                f"papers/research paper/{name}",
                dict(
                    title=name.removesuffix(".pdf"),
                    data_class=C.RESEARCH,
                    publisher=None,
                    coverage="published study",
                    geography="varies",
                    indicators="findings, not case counts",
                ),
            )
        )
    return rows


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--uploads", type=Path, required=True)
    parser.add_argument("--extracted", type=Path, required=True)
    args = parser.parse_args()
    roots = {"uploads": args.uploads, "extracted": args.extracted}
    records = []
    for dataset_id, where, rel, kw in spec():
        path = roots[where] / rel
        if where == "uploads" and not path.exists():  # uploads carry an 8-hex prefix
            matches = list(roots[where].glob("[0-9a-f]" * 8 + f"-{rel}"))
            if len(matches) != 1:
                raise SystemExit(f"cannot find {rel} in {roots[where]}")
            path = matches[0]
        kw.setdefault("publisher", None)
        kw.setdefault("download_url", None)
        kw.setdefault("licence_name", None)
        kw.setdefault("licence_state", L.UNVERIFIED)
        kw.setdefault("licence_evidence_url", None)
        records.append(
            DatasetRecord(
                dataset_id=dataset_id,
                filename=Path(rel).name,
                sha256=sha256_file(path),
                size_bytes=path.stat().st_size,
                **kw,
            )
        )
    sys.stdout.write(dump_manifest(records))


if __name__ == "__main__":
    main()
