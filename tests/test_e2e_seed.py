"""Tests for the E2E seed tool (scripts/e2e_seed.py)."""

from __future__ import annotations

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

from app.data.models import MedicalReport, MetricRecord, ReportFile, UserSession
from app.schema.evidence import EvidenceMatchResponse
from app.service.report_ownership import subject_storage_id
from app.service.sessions import session_hash
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


def test_seed_creates_a_ticket_subject_and_session(database_url):
    """#172 之后种子不再建账号：主体由等价于「票据已兑换」的状态产生。

    断言的是**主体标识的形状**与会话可用性——不再有任何密码可验（密码整体退役）。
    """
    payload = seed_database(
        database_url,
        tenant_id="seed-tenant",
        external_subject="seed-user",
        display_name="种子用户",
    )
    subject = payload["subject"]
    assert subject["owner_id"] == subject_storage_id("seed-tenant", "seed-user")
    assert subject["display_name"] == "种子用户"

    with _session(database_url) as session:
        saved = session.scalar(select(UserSession).where(UserSession.account_id == subject["owner_id"]))
        assert saved is not None
        assert saved.revoked_at is None
        # 会话行里装的是主体标识，不是账号主键。
        assert saved.token_hash == session_hash(subject["session_token"])
        assert session.get(UserSession, saved.id).account_id == subject["owner_id"]


def test_seed_creates_sqlite_database_directory(tmp_path):
    database_dir = tmp_path / "missing" / "nested"
    database_url = f"sqlite:///{database_dir / 'seed.db'}"
    payload = seed_database(
        database_url,
        tenant_id="nested-tenant",
        external_subject="nested-user",
    )

    assert database_dir.is_dir()
    assert (database_dir / "seed.db").is_file()
    assert payload["subject"]["external_subject"] == "nested-user"


def test_seed_creates_completed_and_pending_reports(database_url):
    payload = seed_database(database_url, tenant_id="owner-tenant", external_subject="owner-user")
    statuses = [report["status"] for report in payload["reports"]]
    assert statuses == list(SEED_REPORT_STATUSES)

    with _session(database_url) as session:
        owner_id = payload["subject"]["owner_id"]
        completed = next(item for item in payload["reports"] if item["status"] == "assessed")
        pending = next(item for item in payload["reports"] if item["status"] == "pending_confirmation")

        completed_row = session.get(MedicalReport, completed["id"])
        pending_row = session.get(MedicalReport, pending["id"])
        for row in (completed_row, pending_row):
            assert row is not None
            assert row.owner_id == owner_id
            assert row.patient_id == owner_id
            # 令牌机制已退役：种子不再产出令牌，行里也不再有它的哈希。
            assert row.access_token_hash is None

        # 已完成报告:契约合法的 evidence_result + 已确认指标。
        EvidenceMatchResponse.model_validate(completed_row.evidence_result)
        completed_metrics = session.scalars(
            select(MetricRecord).where(MetricRecord.report_id == completed_row.id)
        ).all()
        # 前三条是原有的血脂场景;后两条是异常判定的分歧样本
        # (模型误标 H 但数值在范围内、模型漏标但数值超范围)。
        assert len(completed_metrics) == 5
        assert all(
            metric.confirmation_status == "confirmed" and metric.confirmed_value and metric.confirmed_reference_range
            for metric in completed_metrics
        )
        by_name = {metric.metric_name: metric for metric in completed_metrics}
        assert by_name["误标的餐后血糖"].abnormal_flag == "H"
        assert by_name["误标的餐后血糖"].metric_value == "5.0"
        assert by_name["漏标的总胆固醇"].abnormal_flag is None
        assert by_name["漏标的总胆固醇"].metric_value == "6.9"

        # 待确认报告:尚未评估,指标等待核对。两条异常(H,确认页显示)+ 一条正常
        # (确认页默认不显示)—— 后者是「隐藏的行如实说没动过」那类断言的夹具(#203)。
        assert pending_row.evidence_result is None
        pending_metrics = session.scalars(select(MetricRecord).where(MetricRecord.report_id == pending_row.id)).all()
        assert len(pending_metrics) == 3
        assert all(
            metric.confirmation_status == "pending" and metric.confirmed_value is None for metric in pending_metrics
        )
        pending_by_name = {metric.metric_name: metric for metric in pending_metrics}
        assert pending_by_name["血红蛋白"].abnormal_flag == "N"
        # 种子回报的行名→id 映射:用例按名字认行(自增 id 会随夹具漂移)。
        reported = next(item for item in payload["reports"] if item["id"] == pending_row.id)
        assert reported["metrics"] == [
            {"id": metric.id, "metric_name": metric.metric_name} for metric in pending_metrics
        ]
        # 坐标感知契约:指标携带页码、坐标与证据原文。
        for metric in (*completed_metrics, *pending_metrics):
            assert metric.page_number == 1
            assert metric.bbox and metric.bbox_normalized
            assert metric.evidence_text


def test_seed_creates_report_page_file_when_dir_provided(database_url, tmp_path):
    report_files_dir = tmp_path / "report-files"
    payload = seed_database(
        database_url,
        tenant_id="files-tenant",
        external_subject="files-user",
        reports=["assessed"],
        report_files_dir=str(report_files_dir),
    )
    assessed = next(item for item in payload["reports"] if item["status"] == "assessed")

    with _session(database_url) as session:
        saved = session.scalar(select(ReportFile).where(ReportFile.report_id == assessed["id"]))

    assert saved is not None
    assert saved.file_index == 1
    assert saved.media_type == "image/png"
    assert saved.page_count == 1
    assert Path(saved.stored_path).is_file()


def test_seed_rejects_unknown_status(database_url):
    with pytest.raises(ValueError, match="不支持的种子报告状态"):
        seed_database(
            database_url,
            tenant_id="x-tenant",
            external_subject="x-user",
            reports=["archived"],
        )


def test_main_prints_json_payload(database_url, capsys):
    exit_code = main(
        [
            "--database",
            database_url,
            "--tenant-id",
            "cli-tenant",
            "--external-subject",
            "cli-user",
        ]
    )
    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["subject"]["external_subject"] == "cli-user"
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


def test_main_rejects_duplicate_subject(database_url, capsys):
    args = [
        "--database",
        database_url,
        "--tenant-id",
        "dup-tenant",
        "--external-subject",
        "dup-user",
    ]
    assert main(args) == 0
    assert main(args) == 1
    assert "主体已存在" in capsys.readouterr().err
