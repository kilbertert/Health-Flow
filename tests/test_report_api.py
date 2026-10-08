"""Tests for Report API."""

import io
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.data.models import Base, MedicalReport, ReportExtractionJob
from app.data.models import MetricRecord as MetricModel
from app.schema.report import MetricRecord
from app.service.report_worker import run_next_job
from app.service.sessions import SESSION_COOKIE, issue_session


class MockVisionService:
    """Mock VisionEncoder service."""

    def parse(self, content, filename):
        from app.service.vision_encoder import ParsedReport

        return ParsedReport(
            report_type="text_pdf",
            raw_text="空腹血糖: 6.5 mmol/L",
            metrics=[
                MetricRecord(
                    metric_name="空腹血糖",
                    metric_value="6.5",
                    unit="mmol/L",
                    evidence_text="空腹血糖 6.5 mmol/L",
                )
            ],
            page_count=1,
            success=True,
        )


@pytest.fixture
def client():
    """Create test client with mocked dependencies."""
    with (
        patch("app.data.mysql_client.get_mysql_client") as mock_mysql,
        patch(
            "app.service.vision_encoder.get_vision_encoder_service",
            return_value=MockVisionService(),
        ),
    ):
        # Mock MySQL
        mock_client = MagicMock()
        mock_mysql.return_value = mock_client

        from app.main import app

        yield TestClient(app)


def test_upload_report_endpoint(client, tmp_path):
    """Test report upload endpoint against a real in-memory SQLite database."""
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine)

    def override_get_db():
        with SessionLocal() as session:
            yield session

    settings = SimpleNamespace(
        MAX_UPLOAD_FILES=20,
        MAX_UPLOAD_BYTES=20 * 1024 * 1024,
        MAX_UPLOAD_TOTAL_BYTES=50 * 1024 * 1024,
        REPORT_PARSE_WORKERS=4,
        REPORT_FILES_DIR=str(tmp_path),
    )
    # #172：上传必须带主体会话（报告令牌旁路已移除）。这条会话等价于
    # 「用户刚从商城跳进来兑换过一张票」。
    owner_id = "account:api-tenant:api-user"
    token, session_row = issue_session(owner_id)
    with SessionLocal() as bootstrap:
        bootstrap.add(session_row)
        bootstrap.commit()

    with (
        patch(
            "app.api.report.get_vision_encoder_service",
            return_value=MockVisionService(),
        ),
        patch("app.api.report.get_settings", return_value=settings),
        patch(
            "app.api.report.resolve_owner",
            return_value=SimpleNamespace(storage_id=owner_id, subject=f"account:{owner_id}"),
        ),
        patch("app.data.get_db", override_get_db),
    ):
        fake_image = b"fake png content"
        cookies = {SESSION_COOKIE: token}

        response = client.post(
            "/api/health/report/upload",
            data={"patient_id": "P001", "department": "内分泌科"},
            files={"file": ("test.png", io.BytesIO(fake_image), "text/html")},
            cookies=cookies,
        )

        assert response.status_code == 202, response.text
        data = response.json()
        assert data["id"] is not None
        assert data["patient_id"] == "P001"
        assert data["department"] == "内分泌科"
        assert data["report_type"] == "体检"
        assert data["status"] == "processing"
        assert data["extraction_job"]["status"] == "queued"
        assert data["extraction_job"]["attempt_count"] == 0
        assert isinstance(data["metrics"], list)
        # 响应里不再回显任何令牌字段（#172）——断言"没有"，而不是"删了就自然没有"。
        assert "access_token" not in data
        assert not [key for key in data if "token" in key.lower()]
        assert run_next_job(SessionLocal) is not None
        parsed_response = client.get(f"/api/health/report/{data['id']}", cookies=cookies)
        parsed = parsed_response.json()
        # 属于**另一个**主体的请求看不到（原来这条断言的是"带错令牌被拒"）。
        # 不能用"不带会话"来测：本用例把 resolve_owner 打了桩，那条路径测不到。
        with patch(
            "app.api.report.resolve_owner",
            return_value=SimpleNamespace(storage_id="account:api-tenant:someone-else"),
        ):
            assert client.get(f"/api/health/report/{data['id']}").status_code == 404
        assert parsed["status"] == "pending_confirmation"
        assert parsed["extraction_job"]["status"] == "completed"
        assert parsed["extraction_job"]["attempt_count"] == 1
        assert len(parsed["metrics"]) == 1

        # A worker crash after parsing but before acknowledging the job must not
        # duplicate metrics when the durable job is retried.
        with SessionLocal() as session:
            job = session.query(ReportExtractionJob).one()
            job.status = "queued"
            session.commit()
        assert run_next_job(SessionLocal) is not None
        with SessionLocal() as session:
            assert session.query(MetricModel).count() == 1

        assert parsed["files"] == [
            {
                "file_index": 1,
                "original_filename": "test.png",
                "media_type": "image/png",
                "page_count": 1,
                "source_url": f"/api/health/report/{data['id']}/files/1/pages/1",
            }
        ]
        source = client.get(parsed["files"][0]["source_url"], cookies=cookies)
        assert source.status_code == 200
        assert source.content == fake_image
        stored = tmp_path / str(data["id"]) / "1.png"
        assert stored.is_file()
        assert client.delete(f"/api/health/report/{data['id']}", cookies=cookies).status_code == 200
        assert not stored.exists()


def test_metric_catalog_proxy_returns_evidence_service_catalog(client):
    catalog = [{"code": "fasting_glucose", "label": "空腹血糖"}]
    with patch("app.api.report.fetch_metric_catalog", return_value=catalog):
        response = client.get("/api/health/metric-catalog")

    assert response.status_code == 200
    assert response.json() == catalog


def test_get_report_endpoint_not_found(client):
    """Test getting non-existent report."""
    with patch("app.data.get_db") as mock_db:
        mock_session = MagicMock()
        mock_db.return_value = mock_session
        mock_session.query.return_value.filter.return_value.first.return_value = None

        response = client.get("/api/health/report/999")

        # Should return 404
        assert response.status_code == 404


def test_upload_rejects_an_extension_outside_the_accepted_set(client):
    """闸门的第一道：不受理的后缀返回 415。

    这条以前**完全没有请求级测试** —— `ALLOWED_EXTENSIONS` 是模块级常量，
    没有人从请求侧钉过它。
    """
    response = client.post(
        "/api/health/report/upload",
        data={"patient_id": "P001"},
        files={"file": ("报告.txt", io.BytesIO(b"hello"), "text/plain")},
    )

    assert response.status_code == 415
    assert response.json()["detail"] == "仅支持 PDF 或常见图片格式"


def test_upload_rejects_content_that_contradicts_the_extension(client):
    """把 PNG 改名成 .pdf：按内容判定，闸门明确拒绝，而不是放行后查看原文才失败。"""
    png = b"\x89PNG\r\n\x1a\n" + b"\x00" * 24

    response = client.post(
        "/api/health/report/upload",
        data={"patient_id": "P001"},
        files={"file": ("报告.pdf", io.BytesIO(png), "application/pdf")},
    )

    assert response.status_code == 415
    detail = response.json()["detail"]
    assert "扩展名说的是 PDF" in detail
    assert "实际是 PNG 图片" in detail
    # 错误信息以患者提交的文件名为准，且不泄露服务端路径。
    assert "报告.pdf" in detail or "路径" not in detail


def _real_pdf(pages: int) -> bytes:
    """一张真的能被 `fitz` 打开的 PDF。"""
    import fitz

    document = fitz.open()
    for _ in range(pages):
        document.new_page()
    data = document.tobytes()
    document.close()
    return data


def test_upload_reports_an_unknown_page_count_instead_of_one(client, tmp_path):
    """读不出页数的材料在响应里是「未知」，不是「共 1 页」（#170）。

    同一份响应同时被两个消费者读：报告详情的翻页器，以及 `page_number` 的越界
    校验。两者此前都拿到那个被冒充的 1。
    """
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine)

    def override_get_db():
        with SessionLocal() as session:
            yield session

    settings = SimpleNamespace(
        MAX_UPLOAD_FILES=20,
        MAX_UPLOAD_BYTES=20 * 1024 * 1024,
        MAX_UPLOAD_TOTAL_BYTES=50 * 1024 * 1024,
        REPORT_PARSE_WORKERS=4,
        REPORT_FILES_DIR=str(tmp_path),
    )
    owner_id = "account:api-tenant:page-count"
    token, session_row = issue_session(owner_id)
    with SessionLocal() as bootstrap:
        bootstrap.add(session_row)
        bootstrap.commit()

    with (
        patch("app.api.report.get_vision_encoder_service", return_value=MockVisionService()),
        patch("app.api.report.get_settings", return_value=settings),
        patch(
            "app.api.report.resolve_owner",
            return_value=SimpleNamespace(storage_id=owner_id, subject=f"account:{owner_id}"),
        ),
        patch("app.data.get_db", override_get_db),
        # 渲染路由走的是 `db_dependency`（不是上传那条 get_db 依赖），要一并指到
        # 这份内存库上，否则它会去开一个真的开发库。
        patch("app.api.deps.db_dependency", override_get_db),
    ):
        cookies = {SESSION_COOKIE: token}
        response = client.post(
            "/api/health/report/upload",
            data={"patient_id": "P001"},
            # 有 PDF 头、fitz 打不开 —— 损坏的 PDF。
            files={"file": ("报告.pdf", io.BytesIO(b"%PDF-1.4\n%%EOF\n"), "application/pdf")},
            cookies=cookies,
        )

        assert response.status_code == 202, response.text
        data = response.json()
        assert data["files"][0]["page_count"] is None
        # 契约允许「未知」，不允许用它冒充一个数。
        assert data["files"][0]["page_count"] != 1

        report_id = data["id"]
        # 页数未知时只承认第 1 页 —— 越界校验不能拿「未知」当「共 1 页」用。
        # 第 2 页被拒（旧写法 `page_number > source.page_count` 在 None 上直接
        # 抛 TypeError，是 500 而不是 404）。第 1 页本身渲染不了（内存 PDF 本来
        # 就打不开），如实报 422，而不是假装成功。
        beyond = client.get(f"/api/health/report/{report_id}/files/1/pages/2", cookies=cookies)
        assert beyond.status_code == 404
        first = client.get(f"/api/health/report/{report_id}/files/1/pages/1", cookies=cookies)
        assert first.status_code == 422

    # 相反的一半：真的只有一页的 PDF 仍然是 1（未知不能把正常的单页报告也吞掉）。
    with (
        patch("app.api.report.get_vision_encoder_service", return_value=MockVisionService()),
        patch("app.api.report.get_settings", return_value=settings),
        patch(
            "app.api.report.resolve_owner",
            return_value=SimpleNamespace(storage_id=owner_id, subject=f"account:{owner_id}"),
        ),
        patch("app.data.get_db", override_get_db),
    ):
        one_page = client.post(
            "/api/health/report/upload",
            data={"patient_id": "P001"},
            files={"file": ("单页.pdf", io.BytesIO(_real_pdf(1)), "application/pdf")},
            cookies={SESSION_COOKIE: token},
        )
        assert one_page.json()["files"][0]["page_count"] == 1


def test_upload_rejects_total_size_limit(client):
    settings = SimpleNamespace(
        MAX_UPLOAD_FILES=20,
        MAX_UPLOAD_BYTES=10,
        MAX_UPLOAD_TOTAL_BYTES=3,
    )
    with patch("app.api.report.get_settings", return_value=settings):
        response = client.post(
            "/api/health/report/upload",
            data={"patient_id": "P001"},
            files={"file": ("test.pdf", io.BytesIO(b"four"), "application/pdf")},
        )

    assert response.status_code == 413
    assert response.json()["detail"] == "报告文件总大小超过限制"


def test_list_reports_endpoint(client):
    """The old cross-report listing route is intentionally frozen."""
    response = client.get("/api/health/reports")
    assert response.status_code == 404


def test_delete_report_not_found(client):
    """Test deleting non-existent report."""
    with patch("app.data.get_db") as mock_db:
        mock_session = MagicMock()
        mock_db.return_value = mock_session
        mock_session.query.return_value.filter.return_value.first.return_value = None

        response = client.delete("/api/health/report/999")

        assert response.status_code == 404


def test_metric_responses_carry_the_server_inferred_flag(client, tmp_path):
    """响应里的 `inferred_abnormal_flag` 与服务端判定是同一个答案。

    这条断言覆盖的是**契约**，不是内部结构：同一个值在响应中出现一次，
    由服务端算好，前端不再推导。既有 test_abnormal_flag_overrides_model_flag
    一类的调用方（`build_observations_with_unmatched`）与它共用同一实现。
    """
    from types import SimpleNamespace

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine)

    def override_get_db():
        with SessionLocal() as session:
            yield session

    owner_id = "account:api-tenant:flag-subject"
    token, session_row = issue_session(owner_id)
    with SessionLocal() as bootstrap:
        report = MedicalReport(patient_id=owner_id, owner_id=owner_id, status="assessed")
        bootstrap.add(report)
        bootstrap.flush()
        bootstrap.add_all(
            [
                # 模型标 H，数值在范围内 —— 响应显示 N。
                MetricModel(
                    report_id=report.id,
                    metric_name="误标偏高",
                    metric_value="5.2",
                    unit="mmol/L",
                    reference_range="3.9-6.1",
                    abnormal_flag="H",
                    page_number=1,
                    evidence_text="误标偏高 5.2 mmol/L",
                ),
                # 模型未标，数值超范围 —— 响应显示 H。
                MetricModel(
                    report_id=report.id,
                    metric_name="漏标",
                    metric_value="6.5",
                    unit="mmol/L",
                    reference_range="3.9-6.1",
                    page_number=1,
                    evidence_text="漏标 6.5 mmol/L",
                ),
                # 患者修正过 —— 按确认值判定为 H（模型值 5.2 本来判 N）。
                MetricModel(
                    report_id=report.id,
                    metric_name="已修正",
                    metric_value="5.2",
                    unit="mmol/L",
                    reference_range="3.9-6.1",
                    confirmed_value="7.1",
                    confirmation_status="corrected",
                    page_number=1,
                    evidence_text="已修正 5.2 mmol/L",
                ),
                # 判定不可定（多数字值）—— 响应为 None，不猜测。
                MetricModel(
                    report_id=report.id,
                    metric_name="多值",
                    metric_value="6.5/7.2",
                    unit="mmol/L",
                    reference_range="3.9-6.1",
                    abnormal_flag="H",
                    page_number=1,
                    evidence_text="多值 6.5/7.2 mmol/L",
                ),
            ]
        )
        bootstrap.add(session_row)
        bootstrap.commit()
        report_id = report.id

    with (
        patch("app.data.get_db", override_get_db),
        patch(
            "app.api.report.resolve_owner",
            return_value=SimpleNamespace(storage_id=owner_id, subject=f"account:{owner_id}"),
        ),
    ):
        cookies = {SESSION_COOKIE: token}
        listed = client.get(f"/api/health/report/{report_id}/metrics", cookies=cookies)
        detail = client.get(f"/api/health/report/{report_id}", cookies=cookies)

    assert listed.status_code == 200, listed.text
    assert detail.status_code == 200, detail.text

    by_name = {item["metric_name"]: item for item in listed.json()}
    assert by_name["误标偏高"]["inferred_abnormal_flag"] == "N"
    assert by_name["漏标"]["inferred_abnormal_flag"] == "H"
    assert by_name["已修正"]["inferred_abnormal_flag"] == "H"
    assert by_name["多值"]["inferred_abnormal_flag"] is None
    # 原始标记原样保留（它是抽取产物，仍然可追溯）——判定回退映射要用它。
    assert by_name["误标偏高"]["abnormal_flag"] == "H"

    # 同一份报告的两个入口给出同一答案（判定的唯一性在契约上可验证）。
    assert {m["metric_name"]: m["inferred_abnormal_flag"] for m in detail.json()["metrics"]} == {
        "误标偏高": "N",
        "漏标": "H",
        "已修正": "H",
        "多值": None,
    }


def test_metric_responses_carry_the_effective_value(client, tmp_path):
    """响应里的 `effective_*` 与服务端生效值一致（#143 的契约）。

    收敛前前端自己抄 `confirmed_x || x`：报告单抄了、确认页卡片与修正草稿漏抄，
    于是同一份报告在两个界面显示两个「结果」。现在服务端把它算好透出。
    """
    from types import SimpleNamespace

    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine)

    def override_get_db():
        with SessionLocal() as session:
            yield session

    owner_id = "account:api-tenant:effective-subject"
    token, session_row = issue_session(owner_id)
    with SessionLocal() as bootstrap:
        report = MedicalReport(patient_id=owner_id, owner_id=owner_id, status="assessed")
        bootstrap.add(report)
        bootstrap.flush()
        bootstrap.add_all(
            [
                # 患者修正过：生效值取修正值。
                MetricModel(
                    report_id=report.id, metric_name="已修正", metric_value="6.5", unit="mmol/L",
                    reference_range="3.9-6.1", confirmed_value="6.4", confirmed_unit="mmol/L",
                    confirmed_reference_range="3.9-6.1", confirmed_evidence_text="已修正 6.4 mmol/L",
                    evidence_text="已修正 6.5 mmol/L", confirmation_status="corrected", page_number=1,
                ),
                # 还没核对：生效值是模型值（临时）。
                MetricModel(
                    report_id=report.id, metric_name="待核对", metric_value="5.2", unit="mmol/L",
                    reference_range="3.9-6.1", evidence_text="待核对 5.2 mmol/L",
                    confirmation_status="pending", page_number=1,
                ),
                # 患者排除：没有生效值。
                MetricModel(
                    report_id=report.id, metric_name="已排除", metric_value="7.5", unit="mmol/L",
                    reference_range="3.9-6.1", evidence_text="已排除 7.5 mmol/L",
                    confirmation_status="excluded", page_number=1,
                ),
            ]
        )
        bootstrap.add(session_row)
        bootstrap.commit()
        report_id = report.id

    with (
        patch("app.data.get_db", override_get_db),
        patch(
            "app.api.report.resolve_owner",
            return_value=SimpleNamespace(storage_id=owner_id, subject=f"account:{owner_id}"),
        ),
    ):
        listed = client.get(f"/api/health/report/{report_id}/metrics", cookies={SESSION_COOKIE: token})

    assert listed.status_code == 200, listed.text
    by_name = {item["metric_name"]: item for item in listed.json()}
    assert by_name["已修正"]["effective_value"] == "6.4"
    assert by_name["已修正"]["effective_evidence_text"] == "已修正 6.4 mmol/L"
    assert by_name["待核对"]["effective_value"] == "5.2"
    assert by_name["已排除"]["effective_value"] is None
    assert by_name["已排除"]["effective_unit"] is None
    assert by_name["已排除"]["effective_reference_range"] is None
    assert by_name["已排除"]["effective_evidence_text"] is None
    # 双值列仍在（审计要回答「抽取原文是什么、患者改成什么」）。
    assert by_name["已修正"]["metric_value"] == "6.5"
    assert by_name["已修正"]["confirmed_value"] == "6.4"
