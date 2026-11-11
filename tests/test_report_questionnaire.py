"""#295：问卷端点 —— 词表校验、状态推导、并发裁决、幂等、越权与级联清除。

钉住的是**端点语义**：校验拒绝词表外组合（422）、旧并发凭据不覆盖新答案（409）、
同内容重放幂等、跳过/部分/重答各是合法状态、旧版本行读出「未答」但不丢、
越权访问与「不存在」同形（404）、删报告级联清除答案行。
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.data.models import Base, MedicalReport, ReportQuestionnaireResponse
from app.service.questionnaire_vocabulary import QUESTIONNAIRE_VERSION
from app.service.sessions import SESSION_COOKIE, issue_session

OWNER = "account:api-tenant:questionnaire"


@pytest.fixture()
def rig():
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

    token, session_row = issue_session(OWNER)
    with SessionLocal() as bootstrap:
        bootstrap.add(session_row)
        bootstrap.add(
            MedicalReport(
                patient_id="P-q",
                owner_id=OWNER,
                status="assessed",
                report_type="体检",
            )
        )
        bootstrap.commit()
        report_id = bootstrap.query(MedicalReport).one().id

    patches = [
        patch(
            "app.api.report.resolve_owner",
            return_value=SimpleNamespace(storage_id=OWNER, subject=f"account:{OWNER}"),
        ),
        patch("app.data.get_db", override_get_db),
    ]
    for item in patches:
        item.start()
    from app.main import app

    yield TestClient(app), SessionLocal, report_id, {SESSION_COOKIE: token}
    for item in reversed(patches):
        item.stop()


def _submit(answers=None, *, skipped=False, version=QUESTIONNAIRE_VERSION, base=0):
    return {
        "questionnaire_version": version,
        "answers": answers or {},
        "skipped": skipped,
        "base_revision": base,
    }


def _url(report_id):
    return f"/api/health/report/{report_id}/questionnaire"


def test_get_without_row_is_unanswered(rig):
    client, _session, report_id, cookies = rig
    response = client.get(_url(report_id), cookies=cookies)
    assert response.status_code == 200
    state = response.json()
    assert state["status"] == "unanswered"
    assert state["revision"] == 0
    assert state["answers"] == {}
    assert len(state["questions"]) == 4
    assert state["tips"] == []


@pytest.mark.parametrize(
    ("submission", "fragment"),
    [
        (_submit({"sleep": "regular"}, version="old-v0"), "版本"),
        (_submit({"mood": "calm"}), "题目"),
        (_submit({"sleep": "perfect"}), "选项"),
    ],
)
def test_submit_rejects_out_of_vocabulary(rig, submission, fragment):
    client, _session, report_id, cookies = rig
    response = client.put(_url(report_id), json=submission, cookies=cookies)
    assert response.status_code == 422
    assert fragment in response.json()["detail"]


def test_partial_submit_persists_and_derives_tips(rig):
    client, _session, report_id, cookies = rig
    response = client.put(_url(report_id), json=_submit({"sleep": "short"}), cookies=cookies)
    assert response.status_code == 200
    state = response.json()
    assert state["status"] == "partial"
    assert state["revision"] == 1
    assert state["answers"] == {"sleep": "short"}
    # 个性化提示注明引用了哪个回答 —— 「基于你回答的睡眠」。
    assert [t["question_id"] for t in state["tips"]] == ["sleep"]
    assert state["tips"][0]["option_id"] == "short"

    reread = client.get(_url(report_id), cookies=cookies).json()
    assert reread["status"] == "partial"
    assert reread["answers"] == {"sleep": "short"}
    assert reread["revision"] == 1


def test_answered_status_and_only_flagged_options_produce_tips(rig):
    client, _session, report_id, cookies = rig
    response = client.put(
        _url(report_id),
        json=_submit(
            {
                "sleep": "short",
                "diet": "balanced",
                "exercise": "sedentary",
                "stress": "calm",
            }
        ),
        cookies=cookies,
    )
    state = response.json()
    assert state["status"] == "answered"
    # 「均衡/平稳」这类无风险信号不出提示 —— 提示只覆盖值得提示的回答。
    assert {t["question_id"] for t in state["tips"]} == {"sleep", "exercise"}


def test_skipped_is_a_statement_and_empties_answers(rig):
    client, _session, report_id, cookies = rig
    # 先答过，再选择跳过 —— 「选择不答」覆盖答案，但仍可重答。
    client.put(_url(report_id), json=_submit({"sleep": "short"}), cookies=cookies)
    response = client.put(
        _url(report_id),
        json=_submit({"diet": "heavy"}, skipped=True, base=1),
        cookies=cookies,
    )
    state = response.json()
    assert state["status"] == "skipped"
    assert state["answers"] == {}
    assert state["tips"] == []


def test_reanswer_updates_in_place_and_bumps_revision(rig):
    client, SessionLocal, report_id, cookies = rig
    client.put(_url(report_id), json=_submit({"sleep": "short"}), cookies=cookies)
    response = client.put(
        _url(report_id),
        json=_submit({"sleep": "troubled", "diet": "heavy"}, base=1),
        cookies=cookies,
    )
    state = response.json()
    assert state["revision"] == 2
    assert state["answers"] == {"sleep": "troubled", "diet": "heavy"}
    assert state["status"] == "partial"
    with SessionLocal() as session:
        rows = session.query(ReportQuestionnaireResponse).all()
    assert len(rows) == 1  # 重答是 UPDATE，一份报告一行


def test_stale_base_revision_cannot_overwrite_newer_answers(rig):
    client, _session, report_id, cookies = rig
    client.put(_url(report_id), json=_submit({"sleep": "short"}), cookies=cookies)
    # 旧凭据 + 新内容 → 并发旧请求，拒绝且不覆盖。
    conflict = client.put(_url(report_id), json=_submit({"diet": "heavy"}, base=0), cookies=cookies)
    assert conflict.status_code == 409
    assert client.get(_url(report_id), cookies=cookies).json()["answers"] == {"sleep": "short"}


def test_replayed_identical_submission_is_idempotent(rig):
    client, _session, report_id, cookies = rig
    payload = _submit({"sleep": "short"})
    first = client.put(_url(report_id), json=payload, cookies=cookies)
    replay = client.put(_url(report_id), json=payload, cookies=cookies)
    assert first.status_code == 200
    assert replay.status_code == 200  # 同内容重放不按冲突报错
    assert replay.json()["revision"] == 1  # 也不递增版本


def test_empty_answers_is_partial_not_skipped(rig):
    client, _session, report_id, cookies = rig
    response = client.put(_url(report_id), json=_submit({}), cookies=cookies)
    assert response.json()["status"] == "partial"


def test_stale_version_row_reads_as_unanswered_but_keeps_revision(rig):
    client, SessionLocal, report_id, cookies = rig
    with SessionLocal() as session:
        session.add(
            ReportQuestionnaireResponse(
                report_id=report_id,
                owner_id=OWNER,
                questionnaire_version="old-v0",
                status="answered",
                answers={"sleep": "regular"},
                revision=3,
            )
        )
        session.commit()
    state = client.get(_url(report_id), cookies=cookies).json()
    assert state["status"] == "unanswered"
    assert state["answers"] == {}
    assert state["revision"] == 3  # 行保留，重答可接着这个凭据覆盖
    fresh = client.put(_url(report_id), json=_submit({"sleep": "short"}, base=3), cookies=cookies)
    assert fresh.status_code == 200
    assert fresh.json()["status"] == "partial"


def test_other_owner_is_not_found_and_creates_nothing(rig):
    client, SessionLocal, report_id, _cookies = rig
    with patch(
        "app.api.report.resolve_owner",
        return_value=SimpleNamespace(
            storage_id="account:api-tenant:someone-else",
            subject="account:account:api-tenant:someone-else",
        ),
    ):
        assert client.get(_url(report_id)).status_code == 404
        assert client.put(_url(report_id), json=_submit({"sleep": "short"})).status_code == 404
    with SessionLocal() as session:
        assert session.query(ReportQuestionnaireResponse).count() == 0


def test_questionnaire_does_not_rewrite_report_facts(rig):
    client, SessionLocal, report_id, cookies = rig
    client.put(
        _url(report_id),
        json=_submit(
            {
                "sleep": "short",
                "diet": "heavy",
                "exercise": "sedentary",
                "stress": "high",
            }
        ),
        cookies=cookies,
    )
    report = client.get(f"/api/health/report/{report_id}", cookies=cookies).json()
    assert report["status"] == "assessed"
    assert report["metrics"] == []
    # 问卷状态摘要随报告出域 —— 患者入口看得到「已答」。
    assert report["questionnaire"]["status"] == "answered"


def test_report_deletion_cascades_the_questionnaire_row(rig):
    client, SessionLocal, report_id, cookies = rig
    client.put(_url(report_id), json=_submit({"sleep": "short"}), cookies=cookies)
    with SessionLocal() as session:
        assert session.query(ReportQuestionnaireResponse).count() == 1
    assert client.delete(f"/api/health/report/{report_id}", cookies=cookies).status_code == 200
    with SessionLocal() as session:
        assert session.query(ReportQuestionnaireResponse).count() == 0
