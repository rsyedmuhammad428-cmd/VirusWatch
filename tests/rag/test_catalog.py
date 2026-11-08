"""Tests for app.rag.catalog: the explicit-URL source list."""

from __future__ import annotations

from app.rag.catalog import INITIAL_SOURCES, sources_for
from app.rag.models import SourceKind


def test_every_source_has_a_specific_document_url_not_a_bare_homepage():
    bare_homepages = {"https://www.who.int/", "https://www.who.int", "https://www.cdc.gov/"}
    for s in INITIAL_SOURCES:
        assert s.url not in bare_homepages
        assert s.url.startswith("https://")


def test_every_source_has_non_blank_title_and_publisher():
    for s in INITIAL_SOURCES:
        assert s.title.strip()
        assert s.publisher.strip()


def test_slugs_are_unique():
    slugs = [s.slug for s in INITIAL_SOURCES]
    assert len(slugs) == len(set(slugs))


def test_sources_for_all_returns_everything():
    assert sources_for("all") == INITIAL_SOURCES
    assert sources_for("") == INITIAL_SOURCES


def test_sources_for_filters_by_publisher_keyword():
    who_sources = sources_for("who")
    assert who_sources
    assert all("who" in s.slug.lower() or "who" in s.publisher.lower() for s in who_sources)


def test_sources_for_nih_returns_the_pakistan_bulletin():
    # Also matches the PMC entry: its publisher string is "PubMed Central (NIH National
    # Library of Medicine)", which genuinely contains "nih" -- NIH is PubMed's parent agency.
    nih = sources_for("nih")
    bulletin = [s for s in nih if s.source_kind is SourceKind.PDF]
    assert len(bulletin) == 1
    assert bulletin[0].country_code == "PK"


def test_sources_for_unknown_tag_returns_empty():
    assert sources_for("not-a-real-publisher") == ()


def test_pmc_source_is_marked_research_not_official():
    pmc = [s for s in INITIAL_SOURCES if "pmc" in s.slug][0]
    assert pmc.authority_level == "peer_reviewed"
    assert pmc.document_type == "scientific_paper"


def test_all_five_priority_diseases_have_prevention_or_guidance_coverage():
    # DATA PROMPT.txt priority list: influenza, dengue, malaria, measles, COVID-19. Flu/COVID
    # share one CDC respiratory-virus guidance document (disease=None, broad coverage); the
    # other three each need their own explicit, named prevention/guidance document so the
    # "intelligent agent" enhancement (app.ai.workflow) always has something real to cite.
    covered = {s.disease for s in INITIAL_SOURCES if s.disease}
    assert {"dengue", "malaria", "measles"} <= covered
    respiratory_docs = [s for s in INITIAL_SOURCES if s.slug == "cdc_respiratory_virus_guidance"]
    assert len(respiratory_docs) == 1  # flu + COVID-19 guidance, disease=None by design


def test_malaria_and_measles_sources_are_official_guidance():
    for slug in ("cdc_malaria_prevention", "cdc_measles_vaccine_recommendations"):
        src = next(s for s in INITIAL_SOURCES if s.slug == slug)
        assert src.authority_level == "official"
        assert src.publisher == "US Centers for Disease Control and Prevention"
        assert src.url.startswith("https://www.cdc.gov/")
