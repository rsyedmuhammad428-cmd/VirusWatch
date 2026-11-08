"""Every file in docs/samples/ must at least be valid, parseable JSON -- these are read by
people integrating a frontend, so a syntax error there is worse than a missing file."""

from __future__ import annotations

import json
from pathlib import Path

SAMPLES_DIR = Path(__file__).resolve().parents[2] / "docs" / "samples"


def test_samples_directory_exists_and_is_not_empty():
    assert SAMPLES_DIR.is_dir()
    assert list(SAMPLES_DIR.glob("*.json"))


def test_every_sample_file_is_valid_json():
    for path in SAMPLES_DIR.glob("*.json"):
        with path.open() as f:
            json.load(f)  # raises on malformed JSON


def test_every_sample_file_documents_its_own_provenance():
    # Every sample is either synthetic-fixture output (existing Phase 4 samples) or a hand-built
    # schema-accurate example (new Phase 8 samples) -- both say so via a top-level "_note" key,
    # so nobody mistakes either for a real production response.
    for path in SAMPLES_DIR.glob("*.json"):
        with path.open() as f:
            data = json.load(f)
        assert "_note" in data, f"{path.name} is missing a provenance _note"
