"""#292 契约冻结测试：系统分组与问卷的新字段是类型化的、缺省兼容旧报告。

钉住的是**契约形状**，不是装配逻辑——分组装配在 #294、问卷端点在 #295。
"""

from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path

import pytest
from pydantic import ValidationError

repo_root = Path(__file__).resolve().parent.parent
if str(repo_root) not in sys.path:
    sys.path.insert(0, str(repo_root))

from app.data.models import (  # noqa: E402
    MedicalReport,
    ReportQuestionnaireResponse,
)
from app.data.models import (  # noqa: E402
    MetricRecord as MetricRecordModel,
)
from app.schema.report import (  # noqa: E402
    MedicalReportResponse,
    MetricRecord,
    QuestionnaireState,
    QuestionnaireSubmission,
    ReportSystemGroup,
)
from app.service.metric_system_map import (  # noqa: E402
    METRIC_SYSTEM_MAP,
    OTHER_GROUP_KEY,
    SYSTEM_GROUP_KEYS,
    SYSTEM_GROUPS,
)
from app.service.questionnaire_vocabulary import (  # noqa: E402
    QUESTIONNAIRE_OPTION_IDS,
    QUESTIONNAIRE_QUESTION_IDS,
    QUESTIONNAIRE_QUESTIONS,
    QUESTIONNAIRE_STATUSES,
    QUESTIONNAIRE_VERSION,
)


def _report_response(**overrides) -> MedicalReportResponse:
    """一份「旧形状」的报告响应：不含 #292 新增字段的最小合法输入。"""
    base = dict(
        id=1,
        patient_id="account:t:s",
        metrics=[
            MetricRecord(
                id=11,
                metric_name="空腹血糖",
                metric_value="5.2",
                reference_range="<5.2",
            )
        ],
        created_at=datetime(2026, 1, 1),
    )
    base.update(overrides)
    return MedicalReportResponse(**base)


class TestReportResponseCompatibility:
    def test_old_report_shape_serializes_with_empty_new_fields(self) -> None:
        """旧报告（无分组、无问卷）照常通过契约：新字段为 None，不冒充。"""
        response = _report_response()
        assert response.system_groups is None
        assert response.questionnaire is None
        # 既有字段不回归：指标、计数、状态照常。
        assert response.metrics[0].metric_name == "空腹血糖"
        assert response.status == "pending_confirmation"

    def test_system_groups_typed_and_none_means_no_conclusion(self) -> None:
        response = _report_response(
            system_groups=[
                ReportSystemGroup(
                    key="cardiovascular_metabolic",
                    name="心血管与代谢",
                    metric_ids=[11],
                )
            ]
        )
        assert response.system_groups[0].counts.parsed == 0

    def test_questionnaire_summary_rejects_unknown_status(self) -> None:
        with pytest.raises(ValidationError):
            _report_response(
                questionnaire={"status": "in_progress", "questionnaire_version": "v1"}
            )


class TestQuestionnaireVocabulary:
    def test_fixed_four_questions_unique_ids(self) -> None:
        assert len(QUESTIONNAIRE_QUESTIONS) == 4
        ids = [q["id"] for q in QUESTIONNAIRE_QUESTIONS]
        assert ids == list(dict.fromkeys(ids)), "题目 ID 必须唯一"
        assert set(ids) == {"sleep", "diet", "exercise", "stress"}

    def test_every_option_id_unique_per_question_and_indexed(self) -> None:
        for question in QUESTIONNAIRE_QUESTIONS:
            option_ids = [o["id"] for o in question["options"]]
            assert len(option_ids) == len(set(option_ids))
            assert len(option_ids) >= 2, "单选题至少两个选项才有意义"
        assert set(QUESTIONNAIRE_OPTION_IDS) == QUESTIONNAIRE_QUESTION_IDS

    def test_status_vocabulary_is_closed_and_ordered(self) -> None:
        assert QUESTIONNAIRE_STATUSES == (
            "unanswered",
            "skipped",
            "partial",
            "answered",
        )
        assert QUESTIONNAIRE_VERSION

    def test_submission_and_state_shapes(self) -> None:
        submission = QuestionnaireSubmission(
            questionnaire_version=QUESTIONNAIRE_VERSION,
            answers={"sleep": "regular"},
        )
        assert submission.skipped is False
        state = QuestionnaireState(
            questionnaire_version=QUESTIONNAIRE_VERSION,
            status="partial",
            questions=[
                {"id": "sleep", "text": "…", "options": [{"id": "regular", "label": "…"}]}
            ],
            answers={"sleep": "regular"},
            revision=3,
        )
        assert state.status == "partial"
        with pytest.raises(ValidationError):
            QuestionnaireState(
                questionnaire_version=QUESTIONNAIRE_VERSION, status="half"
            )


class TestSystemMapDeclaration:
    def test_every_mapped_key_resolves_to_a_declared_group(self) -> None:
        stray = {code for code, key in METRIC_SYSTEM_MAP.items() if key not in SYSTEM_GROUP_KEYS}
        assert not stray, f"映射指向未声明的分组：{stray}"

    def test_other_is_declared_and_last(self) -> None:
        assert OTHER_GROUP_KEY in SYSTEM_GROUP_KEYS
        assert SYSTEM_GROUPS[-1][0] == OTHER_GROUP_KEY

    def test_group_keys_unique(self) -> None:
        keys = [key for key, _name in SYSTEM_GROUPS]
        assert len(keys) == len(set(keys))


class TestQuestionnaireStorage:
    """落库形状：一份报告一行、随报告级联删除。用内存 SQLite 走真实 create_all。"""

    @pytest.fixture()
    def session(self):
        from sqlalchemy import create_engine
        from sqlalchemy.orm import sessionmaker

        from app.data.models import Base

        engine = create_engine("sqlite://")
        Base.metadata.create_all(engine)
        yield sessionmaker(bind=engine)()

    def _report(self, session) -> MedicalReport:
        report = MedicalReport(patient_id="account:t:s", status="assessed")
        session.add(report)
        session.flush()
        return report

    def test_one_row_per_report_and_cascade(self, session) -> None:
        report = self._report(session)
        row = ReportQuestionnaireResponse(
            report_id=report.id,
            owner_id="account:t:s",
            questionnaire_version=QUESTIONNAIRE_VERSION,
            status="partial",
            answers={"sleep": "regular"},
            revision=1,
        )
        session.add(row)
        session.commit()

        # 级联：删报告带走问卷行（撤回解读同意删报告时同步清除）。
        session.delete(report)
        session.commit()
        assert session.get(ReportQuestionnaireResponse, row.id) is None

    def test_second_row_for_same_report_rejected(self, session) -> None:
        from sqlalchemy.exc import IntegrityError

        report = self._report(session)
        for status in ("skipped", "answered"):
            session.add(
                ReportQuestionnaireResponse(
                    report_id=report.id,
                    owner_id="account:t:s",
                    questionnaire_version=QUESTIONNAIRE_VERSION,
                    status=status,
                    answers={},
                    revision=1,
                )
            )
        with pytest.raises(IntegrityError):
            session.commit()

    def test_existing_tables_untouched_by_new_table(self, session) -> None:
        """新表经 create_all 落地；既有表结构不变（旧库不需要列级迁移）。"""
        report = self._report(session)
        session.add(
            MetricRecordModel(report_id=report.id, metric_name="x", metric_value="1")
        )
        session.commit()
        assert session.query(MetricRecordModel).count() == 1
