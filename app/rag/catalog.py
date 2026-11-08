"""The explicit document catalog (Prompt.txt §10 step 1: "source discovery or explicit URL
ingestion"). Every entry below is a specific, named document URL chosen by a person — never a
homepage, and never an automatically-crawled link — matching the spec's "Use actual document
URLs rather than generic homepage citations" and "Do not treat an arbitrary website as
authoritative merely because its text was retrieved successfully."

Honesty note (see docs/PHASE5_REPORT.md): the `title`/`notes` fields below for the WHO EMRO
dengue page and the CDC respiratory-virus guidance page reflect facts actually returned by a
WebSearch + WebFetch of those live pages on 2026-11-01 (section headings, the publisher's own
page title, no stated last-reviewed date). WebFetch returns a *summarized* rendering of a page,
not verbatim HTML, so these two entries are catalog metadata only -- there is no corresponding
"real" HTML fixture byte-for-byte from that fetch; `tests/rag/test_extract_fixtures.py` instead
runs extract_html() against synthetic HTML that quotes those same real facts. The PubMed/PMC
entry's title, authors, journal, DOI and dates are copied verbatim from the real PMC page.
"""

from __future__ import annotations

from app.rag.models import SourceKind, SourceSpec

INITIAL_SOURCES: tuple[SourceSpec, ...] = (
    SourceSpec(
        slug="who_dengue_fact_sheet",
        url="https://www.emro.who.int/health-topics/dengue/index.html",
        title="Dengue (WHO Regional Office for the Eastern Mediterranean)",
        publisher="World Health Organization",
        document_type="fact_sheet",
        authority_level="official",
        source_kind=SourceKind.HTML,
        country_code=None,
        disease="dengue",
        notes=(
            "Retrieved via WebSearch+WebFetch 2026-11-01; page gives regional overview, "
            "prevention guidance and a 2025 copyright footer but no explicit publication date."
        ),
    ),
    SourceSpec(
        slug="who_outbreak_guidance",
        url="https://www.who.int/health-topics/disease-outbreaks",
        title="Disease Outbreaks",
        publisher="World Health Organization",
        document_type="guidance",
        authority_level="official",
        source_kind=SourceKind.HTML,
        country_code=None,
        disease=None,
        notes="Listed per Prompt.txt §10; not yet fetched (see docs/PHASE5_REPORT.md limitations).",
    ),
    SourceSpec(
        slug="nih_pk_weekly_bulletin_w41_2026",
        url="https://www.nih.org.pk/phb/weekly-bulletin",
        title="Public Health Bulletin - Pakistan, Week 41, 2026",
        publisher="National Institutes of Health, Pakistan",
        document_type="bulletin",
        authority_level="official",
        source_kind=SourceKind.PDF,
        country_code="PK",
        disease=None,
        notes=(
            "The bulletin itself (not the index page) was supplied as an attachment in an "
            "earlier phase of this engagement; see tests/fixtures/rag/nih_bulletin_week41_2026.pdf "
            "(first 3 pages of the real PDF, trimmed only for fixture size)."
        ),
    ),
    SourceSpec(
        slug="cdc_respiratory_virus_guidance",
        url="https://www.cdc.gov/respiratory-viruses/guidance/respiratory-virus-guidance.html",
        title="Respiratory Virus Guidance",
        publisher="US Centers for Disease Control and Prevention",
        document_type="guidance",
        authority_level="official",
        source_kind=SourceKind.HTML,
        country_code="US",
        disease=None,
        notes=(
            "Retrieved via WebSearch+WebFetch 2026-11-01; page is organized into sections "
            "covering core prevention strategies, additional precautions and when sick; no "
            "last-reviewed date was returned."
        ),
    ),
    SourceSpec(
        slug="pmc_dengue_karachi_2024",
        url="https://pmc.ncbi.nlm.nih.gov/articles/PMC10902108",
        title=(
            "Clinical and Epidemiological Characteristics of Dengue Fever Patients in Karachi, "
            "Pakistan"
        ),
        publisher="PubMed Central (NIH National Library of Medicine)",
        document_type="scientific_paper",
        authority_level="peer_reviewed",
        source_kind=SourceKind.HTML,
        country_code="PK",
        disease="dengue",
        notes=(
            "DOI 10.3389/ijph.2024.1606753. Retrieved via WebSearch+WebFetch 2026-11-01; "
            "abstract summary reported 189 patients (172 female, 17 male), 84.7% with fever, "
            "highest caseload in Korangi and lowest in Keamari, no significant lab-value "
            "associations found. Treated as research, never as a current surveillance count "
            "(Prompt.txt: 'distinguish research findings from official recommendations')."
        ),
    ),
    SourceSpec(
        slug="cdc_malaria_prevention",
        url="https://www.cdc.gov/malaria/prevention/index.html",
        title="Preventing Malaria While Traveling",
        publisher="US Centers for Disease Control and Prevention",
        document_type="guidance",
        authority_level="official",
        source_kind=SourceKind.HTML,
        country_code=None,
        disease="malaria",
        notes=(
            "Retrieved via WebSearch+WebFetch 2026-11-07; page states last reviewed/last "
            "updated Mar 12, 2024. Covers antimalarial medication (take only as prescribed by "
            "a clinician, buy before departure to avoid counterfeit drugs abroad), EPA-registered "
            "insect repellent (DEET offers the strongest protection; picaridin, IR3535, oil of "
            "lemon eucalyptus, PMD and 2-undecanone are also listed), permethrin-treated "
            "clothing (0.5% permethrin, never applied to skin), screened/air-conditioned "
            "housing or a permethrin-treated bed net, and seeking immediate medical care -- "
            "mentioning travel history -- for fever or flu-like illness during or up to a year "
            "after travel, since the page states prevention is not 100% effective and malaria "
            "can be fatal. Written for a travel/prevention audience, not a treatment protocol."
        ),
    ),
    SourceSpec(
        slug="cdc_measles_vaccine_recommendations",
        url="https://www.cdc.gov/measles/hcp/vaccine-considerations/",
        title="Measles Vaccine Recommendations",
        publisher="US Centers for Disease Control and Prevention",
        document_type="guidance",
        authority_level="official",
        source_kind=SourceKind.HTML,
        country_code=None,
        disease="measles",
        notes=(
            "Retrieved via WebSearch+WebFetch 2026-11-07; page states last updated Mar 3, 2026, "
            "last reviewed Sep 20, 2024. Clinician-facing MMR/MMRV guidance: two MMR doses "
            "(12-15 months, then 4-6 years, or at least 28 days apart); two doses are 97% "
            "effective (range 67-100%), one dose 93% effective (range 39-100%); accepted "
            "evidence of immunity is a written record, lab evidence, lab-confirmed disease, or "
            "birth before 1957; post-exposure prophylaxis is MMR within 72 hours of exposure or "
            "immune globulin (0.5 mL/kg IM) within 6 days, and the page explicitly warns not to "
            "give MMR and immune globulin at the same time because it invalidates the vaccine. "
            "This is vaccination/post-exposure guidance, not acute-illness home care advice."
        ),
    ),
)


def sources_for(tag: str) -> tuple[SourceSpec, ...]:
    """Filter the catalog by publisher keyword ('who', 'nih', 'cdc', 'pubmed') -- the --source
    value accepted by `python -m app.cli ingest-documents --source <tag>`.
    """
    needle = tag.strip().lower()
    if needle in ("", "all"):
        return INITIAL_SOURCES
    return tuple(
        s for s in INITIAL_SOURCES if needle in s.slug.lower() or needle in s.publisher.lower()
    )
