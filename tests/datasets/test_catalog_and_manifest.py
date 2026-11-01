from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.datasets.catalog import DataClass, DatasetRecord, LicenceState, permissions
from app.datasets.manifest import (
    dump_manifest,
    find_duplicate_content,
    load_manifest,
    sha256_file,
    verify_files,
)


def record(**kw) -> DatasetRecord:
    base = dict(
        dataset_id="d1",
        title="t",
        data_class=DataClass.SURVEILLANCE,
        publisher=None,
        filename="a.csv",
        sha256="0" * 64,
        size_bytes=1,
        download_url=None,
        licence_name=None,
        licence_state=LicenceState.UNVERIFIED,
        licence_evidence_url=None,
        coverage="c",
        geography="g",
        indicators="i",
    )
    base.update(kw)
    return DatasetRecord(**base)


@pytest.mark.parametrize("state", [LicenceState.UNVERIFIED, LicenceState.NONE_STATED])
def test_unverified_or_unstated_licence_allows_nothing_public(state):
    use = permissions(DataClass.SURVEILLANCE, state)
    assert use.internal_validation
    assert not (use.public_display or use.redistribute_raw or use.commercial)


def test_noncommercial_licence_allows_display_but_not_commercial_or_raw_redistribution():
    use = permissions(DataClass.HEALTH_CONTEXT, LicenceState.NONCOMMERCIAL)
    assert use.public_display and not use.commercial and not use.redistribute_raw


def test_open_licence_allows_everything():
    use = permissions(DataClass.SURVEILLANCE, LicenceState.OPEN)
    assert use.public_display and use.redistribute_raw and use.commercial


@pytest.mark.parametrize("cls", [DataClass.RESEARCH, DataClass.COMPILATION])
def test_research_and_compilations_are_never_redistributable_even_if_open(cls):
    use = permissions(cls, LicenceState.OPEN)
    assert not (use.public_display or use.redistribute_raw or use.commercial)


def test_manifest_round_trip_and_duplicate_ids_rejected():
    records = [record(dataset_id="a"), record(dataset_id="b", sha256="1" * 64)]
    assert load_manifest(dump_manifest(records)) == sorted(records, key=lambda r: r.dataset_id)
    payload = json.loads(dump_manifest(records))
    payload["datasets"].append(payload["datasets"][0])
    with pytest.raises(ValueError, match="duplicate"):
        load_manifest(json.dumps(payload))


def test_verify_detects_missing_and_modified_files(tmp_path: Path):
    good = tmp_path / "good.csv"
    good.write_text("x")
    bad = tmp_path / "bad.csv"
    bad.write_text("y")
    records = [
        record(dataset_id="good", filename="good.csv", sha256=sha256_file(good)),
        record(dataset_id="bad", filename="bad.csv", sha256=sha256_file(good)),
        record(dataset_id="gone", filename="gone.csv"),
    ]
    assert verify_files(records, tmp_path) == {"bad": "checksum_mismatch", "gone": "missing"}


def test_identical_bytes_are_reported_as_duplicates():
    records = [
        record(dataset_id="a", sha256="f" * 64),
        record(dataset_id="b", sha256="f" * 64),
        record(dataset_id="c", sha256="e" * 64),
    ]
    assert find_duplicate_content(records) == [["a", "b"]]


def test_committed_manifest_loads_and_never_grants_unverified_permissions():
    path = Path("data/provenance/manifest.json")
    if not path.exists():
        pytest.skip("manifest not generated")
    for r in load_manifest(path.read_text()):
        if r.licence_state in (LicenceState.UNVERIFIED, LicenceState.NONE_STATED):
            assert not r.use.public_display, r.dataset_id
