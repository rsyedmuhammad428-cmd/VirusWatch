"""Provenance manifest: a JSON list of DatasetRecord plus integrity verification of the files."""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict
from pathlib import Path

from app.datasets.catalog import DataClass, DatasetRecord, LicenceState

CHUNK = 1024 * 1024


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(CHUNK), b""):
            digest.update(block)
    return digest.hexdigest()


def dump_manifest(records: list[DatasetRecord]) -> str:
    rows = [
        {**asdict(r), "data_class": r.data_class.value, "licence_state": r.licence_state.value}
        for r in sorted(records, key=lambda r: r.dataset_id)
    ]
    return (
        json.dumps({"manifest_version": 1, "datasets": rows}, indent=2, ensure_ascii=False) + "\n"
    )


def load_manifest(text: str) -> list[DatasetRecord]:
    payload = json.loads(text)
    if payload.get("manifest_version") != 1:
        raise ValueError("unsupported manifest_version")
    records = []
    for row in payload["datasets"]:
        row = dict(row)
        row["data_class"] = DataClass(row["data_class"])
        row["licence_state"] = LicenceState(row["licence_state"])
        records.append(DatasetRecord(**row))
    ids = [r.dataset_id for r in records]
    if len(ids) != len(set(ids)):
        raise ValueError("duplicate dataset_id in manifest")
    return records


def verify_files(records: list[DatasetRecord], directory: Path) -> dict[str, str]:
    """Return {dataset_id: problem} for files that are missing or whose bytes changed."""
    problems: dict[str, str] = {}
    for record in records:
        path = directory / record.filename
        if not path.is_file():
            problems[record.dataset_id] = "missing"
        elif sha256_file(path) != record.sha256:
            problems[record.dataset_id] = "checksum_mismatch"
    return problems


def find_duplicate_content(records: list[DatasetRecord]) -> list[list[str]]:
    """Groups of dataset ids whose bytes are identical (same SHA-256)."""
    by_hash: dict[str, list[str]] = {}
    for r in records:
        by_hash.setdefault(r.sha256, []).append(r.dataset_id)
    return [ids for ids in by_hash.values() if len(ids) > 1]
