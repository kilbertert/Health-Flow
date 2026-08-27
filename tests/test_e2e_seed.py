"""Tests for the E2E seed tool (scripts/e2e_seed.py)."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import sysconfig
from contextlib import contextmanager
from pathlib import Path

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.data.models import MedicalReport, MetricRecord, ReportFile, UserAccount
from app.schema.evidence import EvidenceMatchResponse
from app.service.auth import verify_password
from scripts.e2e_seed import SEED_REPORT_STATUSES, main, seed_database


@pytest.fixture
def database_url(tmp_path):
    return f"sqlite:///{tmp_path / 'e2e-seed.db'}"


@contextmanager
def _session(database_url):
    engine = create_engine(database_url)
    try:
        with Session(engine) as session:
            yield session
    finally:
        engine.dispose()


def test_seed_creates_login_account(database_url):
    payload = seed_database(
        database_url,
        email=" Seed@HealthFlow.test ",
        password="e2e-pass-123",
        display_name="种子用户",
    )
    account = payload["account"]
    assert account["email"] == "seed@healthflow.test"
    assert account["display_name"] == "种子用户"

    with _session(database_url) as session:
        saved = session.scalar(
            select(UserAccount).where(UserAccount.email == account["email"])
        )
    assert saved is not None
    assert verify_password(account["password"], saved.password_hash)
    assert saved.password_hash != account["password"]


def test_seed_creates_sqlite_database_directory(tmp_path):
    database_dir = tmp_path / "missing" / "nested"
    database_url = f"sqlite:///{database_dir / 'seed.db'}"
    payload = seed_database(
        database_url,
        email="nested@healthflow.test",
        password="e2e-pass-123",
    )

    assert database_dir.is_dir()
    assert (database_dir / "seed.db").is_file()
    assert payload["account"]["email"] == "nested@healthflow.test"


def test_seed_creates_completed_and_pending_reports(database_url):
    payload = seed_database(
        database_url, email="owner@healthflow.test", password="e2e-pass-123"
    )
    statuses = [report["status"] for report in payload["reports"]]
    assert statuses == list(SEED_REPORT_STATUSES)

    with _session(database_url) as session:
        account = session.scalar(
            select(UserAccount).where(UserAccount.email == "owner@healthflow.test")
        )
        assert account is not None
        completed = next(
            item for item in payload["reports"] if item["status"] == "assessed"
        )
        pending = next(
            item
            for item in payload["reports"]
            if item["status"] == "pending_confirmation"
        )

        completed_row = session.get(MedicalReport, completed["id"])
        pending_row = session.get(MedicalReport, pending["id"])
        for row, item in ((completed_row, completed), (pending_row, pending)):
            assert row is not None
            assert row.owner_id == account.id
            assert row.patient_id == account.id
            assert row.access_token_hash == hashlib.sha256(
                item["access_token"].encode("utf-8")
            ).hexdigest()

        # 已完成报告:契约合法的 evidence_result + 已确认指标。
        EvidenceMatchResponse.model_validate(completed_row.evidence_result)
        completed_metrics = session.scalars(
            select(MetricRecord).where(MetricRecord.report_id == completed_row.id)
        ).all()
        assert len(completed_metrics) == 3
        assert all(
            metric.confirmation_status == "confirmed"
            and metric.confirmed_value
            and metric.confirmed_reference_range
            for metric in completed_metrics
        )

        # 待确认报告:尚未评估,指标等待核对。
        assert pending_row.evidence_result is None
        pending_metrics = session.scalars(
            select(MetricRecord).where(MetricRecord.report_id == pending_row.id)
        ).all()
        assert len(pending_metrics) == 2
        assert all(
            metric.confirmation_status == "pending"
            and metric.confirmed_value is None
            for metric in pending_metrics
        )
        # 坐标感知契约:指标携带页码、坐标与证据原文。
        for metric in (*completed_metrics, *pending_metrics):
            assert metric.page_number == 1
            assert metric.bbox and metric.bbox_normalized
            assert metric.evidence_text


def test_seed_creates_report_page_file_when_dir_provided(database_url, tmp_path):
    report_files_dir = tmp_path / "report-files"
    payload = seed_database(
        database_url,
        email="files@healthflow.test",
        password="e2e-pass-123",
        reports=["assessed"],
        report_files_dir=str(report_files_dir),
    )
    assessed = next(
        item for item in payload["reports"] if item["status"] == "assessed"
    )

    with _session(database_url) as session:
        saved = session.scalar(
            select(ReportFile).where(ReportFile.report_id == assessed["id"])
        )

    assert saved is not None
    assert saved.file_index == 1
    assert saved.media_type == "image/png"
    assert saved.page_count == 1
    assert Path(saved.stored_path).is_file()


def test_seed_rejects_unknown_status(database_url):
    with pytest.raises(ValueError, match="不支持的种子报告状态"):
        seed_database(
            database_url,
            email="x@healthflow.test",
            password="e2e-pass-123",
            reports=["archived"],
        )


def test_main_prints_json_payload(database_url, capsys):
    exit_code = main(
        [
            "--database",
            database_url,
            "--email",
            "cli@healthflow.test",
            "--password",
            "e2e-pass-123",
        ]
    )
    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["account"]["email"] == "cli@healthflow.test"
    assert {item["status"] for item in payload["reports"]} == {
        "assessed",
        "pending_confirmation",
    }


def test_script_help_runs_without_editable_install(tmp_path):
    """脚本模式在 editable install 失效时仍能自举导入 ``app``。"""
    repo_root = Path(__file__).resolve().parents[1]
    site_packages = Path(sysconfig.get_paths()["purelib"])
    env = {
        **os.environ,
        "PYTHONPATH": str(site_packages),
        "PYTHONDONTWRITEBYTECODE": "1",
    }

    completed = subprocess.run(
        [sys.executable, "-S", str(repo_root / "scripts" / "e2e_seed.py"), "--help"],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    assert "--database" in completed.stdout


def test_main_rejects_duplicate_account(database_url, capsys):
    args = [
        "--database",
        database_url,
        "--email",
        "dup@healthflow.test",
        "--password",
        "e2e-pass-123",
    ]
    assert main(args) == 0
    assert main(args) == 1
    assert "账户已存在" in capsys.readouterr().err
