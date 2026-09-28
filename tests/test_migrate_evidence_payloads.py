"""The stored-payload migration must be idempotent and must not touch healthy rows."""

from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

repo_root = Path(__file__).resolve().parent.parent
if str(repo_root) not in sys.path:
    sys.path.insert(0, str(repo_root))

from scripts.migrate_evidence_payloads import main, migrate_payload  # noqa: E402

LEGACY_PAYLOAD = {
    "schema_version": "3",
    "sorting_version": "published-card-reference-range-v1",
    "correlation_id": "c-1",
    "findings": [
        {
            "condition_code": "COND_DYSLIPIDEMIA",
            "product_status": "available",
            "recommendation_message": "以下为可考虑的健康管理建议",
            "recommendations": [{"product_id": "product-1", "nutrient": "植物甾醇"}],
            "evidence_items": [{"metric_code": "ldl_c"}],
        },
        {"condition_code": "COND_PREDIABETES", "evidence_items": [{"metric_code": "hba1c"}]},
    ],
    "unmatched": [],
    "skipped": [],
    "message": "",
    "patient_reply": {
        "title": "体检报告解读与健康风险提示",
        "summary": "",
        "findings": [
            {
                "condition_code": "COND_DYSLIPIDEMIA",
                "product_status": "available",
                "recommendation_message": "以下为可考虑的健康管理建议",
                "recommendations": [{"product_id": "product-1"}],
            }
        ],
        "unmatched_count": 0,
        "disclaimer": "d",
    },
}


def test_migrate_payload_removes_only_the_retired_fields() -> None:
    payload, removed = migrate_payload(json.loads(json.dumps(LEGACY_PAYLOAD)))

    # 3 fields x (1 finding + 1 patient finding), the second finding carries none
    assert removed == 6
    for finding in payload["findings"]:
        assert "recommendations" not in finding
        assert "recommendation_message" not in finding
        assert "product_status" not in finding
    patient_finding = payload["patient_reply"]["findings"][0]
    assert "recommendations" not in patient_finding
    # everything else survives byte-identical
    assert payload["findings"][0]["condition_code"] == "COND_DYSLIPIDEMIA"
    assert payload["findings"][0]["evidence_items"] == [{"metric_code": "ldl_c"}]
    assert payload["correlation_id"] == "c-1"


def test_nested_card_fields_are_removed_too() -> None:
    """v2 puts the card on the finding; v3 puts it under evidence_items. Both carry it."""

    payload = json.loads(json.dumps(LEGACY_PAYLOAD))
    payload["findings"][0]["card"] = {"scope_key": "metric:ldl_c", "product_status": "available"}
    payload["findings"][0]["evidence_items"][0]["card"] = {
        "scope_key": "metric:ldl_c",
        "product_status": "not_implemented",
    }

    migrated, removed = migrate_payload(payload)

    assert removed == 8  # 6 top-level + 2 nested cards
    assert "product_status" not in migrated["findings"][0]["card"]
    assert "product_status" not in migrated["findings"][0]["evidence_items"][0]["card"]
    assert migrated["findings"][0]["card"]["scope_key"] == "metric:ldl_c"


def test_migrate_payload_is_idempotent() -> None:
    once, _ = migrate_payload(json.loads(json.dumps(LEGACY_PAYLOAD)))
    twice, removed = migrate_payload(json.loads(json.dumps(once)))

    assert removed == 0
    assert twice == once


def _url(database: Path) -> str:
    return f"sqlite:///{database}"


def _seed(database: Path, rows: dict[int, str]) -> None:
    connection = sqlite3.connect(database)
    try:
        connection.execute(
            "CREATE TABLE medical_reports (id INTEGER PRIMARY KEY, evidence_result TEXT)"
        )
        connection.executemany(
            "INSERT INTO medical_reports (id, evidence_result) VALUES (?, ?)", rows.items()
        )
        connection.commit()
    finally:
        connection.close()


def _read(database: Path, report_id: int) -> dict | None:
    connection = sqlite3.connect(database)
    try:
        raw = connection.execute(
            "SELECT evidence_result FROM medical_reports WHERE id = ?", (report_id,)
        ).fetchone()[0]
    finally:
        connection.close()
    return json.loads(raw) if raw else None


def test_dry_run_reports_without_writing(tmp_path) -> None:
    database = tmp_path / "healthflow.db"
    _seed(database, {1: json.dumps(LEGACY_PAYLOAD)})
    before = _read(database, 1)

    assert main(["--database", _url(database), "--dry-run"]) == 0
    assert _read(database, 1) == before


def test_migration_rewrites_legacy_and_leaves_clean_rows_alone(tmp_path) -> None:
    clean = {"schema_version": "3", "findings": [{"condition_code": "COND_X"}]}
    database = tmp_path / "healthflow.db"
    _seed(database, {1: json.dumps(LEGACY_PAYLOAD), 2: json.dumps(clean)})

    assert main(["--database", _url(database)]) == 0

    migrated = _read(database, 1)
    assert migrated["findings"][0] == {
        "condition_code": "COND_DYSLIPIDEMIA",
        "evidence_items": [{"metric_code": "ldl_c"}],
    }
    assert _read(database, 2) == clean

    # second run is a no-op
    assert main(["--database", _url(database)]) == 0
    assert _read(database, 1) == migrated


def test_malformed_payload_is_reported_and_left_intact(tmp_path, capsys) -> None:
    database = tmp_path / "healthflow.db"
    _seed(database, {1: "{not json"})

    assert main(["--database", _url(database)]) == 1
    assert "cannot parse" in capsys.readouterr().err
    connection = sqlite3.connect(database)
    try:
        raw = connection.execute("SELECT evidence_result FROM medical_reports").fetchone()[0]
    finally:
        connection.close()
    assert raw == "{not json"
