"""解读失败块的出域形状（PRD #226 第二张票）。

断言的是**外部行为**：`GET /report/{id}` 给出的失败块里，成因、可重试性、患者动作三者
互不矛盾，且只有两个成因会请患者重传 —— 不断言内部字段名怎么排。

三处半成品（`processing_error` / `processing_warnings` / `extraction_job.error_class`）
的退场由静态守卫拦（见文件末尾）。
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.data.models import Base, ReportExtractionJob
from app.data.models import MedicalReport as ReportModel
from app.service.interpretation_failure import (
    FAILURES_ORDERED,
    PATIENT_ACTION_BY_FAILURE,
    is_retryable,
    patient_action,
)

_REPO = Path(__file__).resolve().parents[1]


def _report_with(parsed_content: dict, *, status: str = "failed"):
    """一份带给定 `parsed_content` 的报告 + 一个已结束的抽取任务。"""
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    with factory() as db:
        report = ReportModel(
            patient_id="P-failure",
            owner_id="unowned",
            status=status,
            parsed_content=parsed_content,
        )
        db.add(report)
        db.flush()
        db.add(
            ReportExtractionJob(
                report_id=report.id,
                status="failed",
                attempt_count=3,
            )
        )
        db.commit()
        return factory, report.id


def _block(factory, report_id):
    from app.api.report import _report_response

    with factory() as db:
        report = db.get(ReportModel, report_id)
        return _report_response(report, []).model_dump()["interpretation_failure"]


# ── 三项互不矛盾 ──────────────────────────────────────────────────────────────


@pytest.mark.parametrize("cause", FAILURES_ORDERED)
def test_the_block_agrees_with_the_vocabulary_for_every_cause(cause):
    """成因 → 可重试 / 患者动作两项都是**派生**读法，不是各自独立的事实。

    三者的唯一口径都在词表模块里，所以这条断言就是 `is_retryable` / `patient_action`
    与出域的块逐条相等 —— 一句「服务端算好一起送出去」的可执行版本。
    """
    factory, report_id = _report_with({"failure": cause, "file_results": []})
    block = _block(factory, report_id)

    assert block["cause"] == cause
    assert block["retryable"] is is_retryable(cause)
    assert block["action"] == patient_action(cause)


def test_only_two_causes_ask_the_patient_to_upload_again():
    """**本 PRD 的患者可见后果。** 「重新上传」只在两种成因下出现。"""
    asking = set()
    for cause in FAILURES_ORDERED:
        factory, report_id = _report_with({"failure": cause, "file_results": []})
        block = _block(factory, report_id)
        if block["action"] == "upload_again":
            asking.add(cause)
    assert asking == {"material_unreadable", "content_unrecognized"}


def test_a_retryable_cause_is_never_an_upload_again_cause():
    """可重试与「请患者重传」不能同时成立：一条说「我们稍后再试」，另一条说「请你再传
    一遍」。同时成立就是让患者做一件白做的事。"""
    for cause, action in PATIENT_ACTION_BY_FAILURE.items():
        assert not (is_retryable(cause) and action == "upload_again"), cause


# ── 失败的文件：只有文件名 ────────────────────────────────────────────────────


def test_failed_files_carry_the_name_and_not_the_error_text():
    """模型/框架的错误原文不出域 —— 它是给排障看的。

    此前 `processing_warnings` 是 `["文件.png: provider timeout"]` 这样的拼接串，
    直接呈现给患者。文件名是患者自己给的，认得出来；错误原文不是。
    """
    factory, report_id = _report_with(
        {
            "failure": "provider_unavailable",
            "file_results": [
                {"file_index": 1, "filename": "好.png", "status": "parsed"},
                {
                    "file_index": 2,
                    "filename": "坏.png",
                    "status": "failed",
                    "error": "provider timeout while calling the model",
                },
            ],
        }
    )
    block = _block(factory, report_id)

    assert block["failed_files"] == [{"file_index": 2, "filename": "坏.png"}]
    assert "provider timeout" not in str(block), "错误原文不许出现在失败块里"


# ── 历史行 ────────────────────────────────────────────────────────────────────


def test_a_legacy_row_without_a_cause_reads_as_no_failure_block():
    """改动之前的行只有 `error`（一句通用话术）与 `warnings`，没有成因。

    那种行读出来是**空失败块**：我们不知道它的成因，就不猜一个。患者侧退回状态级的
    那句话，而不是收到一个编出来的成因。
    """
    factory, report_id = _report_with(
        {
            "error": "报告智能解读失败，请重新上传或稍后重试。",
            "warnings": ["坏.png: provider timeout"],
        }
    )
    assert _block(factory, report_id) is None


def test_a_report_that_never_failed_has_no_block():
    factory, report_id = _report_with({"file_count": 1}, status="pending_confirmation")
    assert _block(factory, report_id) is None


def test_a_row_with_an_unknown_cause_value_is_not_guessed_at():
    """库里存着一个不在词表里的值时，读成「没有成因」而不是原样出域。

    出域契约的 `Literal` 会拒绝它 —— 一个不在词表里的值进不了响应，这是好事；但它
    不该让整个响应 500。
    """
    factory, report_id = _report_with({"failure": "provider_melted", "file_results": []})
    assert _block(factory, report_id) is None


# ── 静态守卫：三处半成品退场 ──────────────────────────────────────────────────


def _code_only(text: str) -> str:
    """去掉注释与文档字符串后的源码。

    注释与文档字符串里**必须**能写下「这里以前有 `processing_error`」—— 那是删掉它
    的理由。守卫拦的是**代码**里再出现它。
    """
    without_docstrings = re.sub(r'""".*?"""', "", text, flags=re.DOTALL)
    return "\n".join(line.split("#", 1)[0] for line in without_docstrings.splitlines())


def test_the_three_half_products_are_gone_from_the_contract():
    """`processing_error` / `processing_warnings` / `error_class` 退场。

    它们是「同一件事有三个名字」的三处半成品：一个不出域、一个出域无人读、一个从不被读。
    新块取代它们之后，再出现任何一个都是第二个入口长回来了。
    """
    schema = _code_only((_REPO / "app" / "schema" / "report.py").read_text(encoding="utf-8"))
    body = re.search(r"class MedicalReportResponse.*?(?=\nclass )", schema, re.DOTALL)
    assert body is not None, "契约类不见了"
    for name in ("processing_error", "processing_warnings", "error_class"):
        assert name not in body.group(0), f"{name} 又回到了响应契约里"


def test_the_guard_can_fail():
    """守卫自己要能被弄红。"""
    planted = "class MedicalReportResponse:\n    processing_warnings: list[str] = []\n"
    assert "processing_warnings" in _code_only(planted)
    assert "processing_warnings" not in _code_only('"""processing_warnings 已退场。"""')


def test_the_response_contract_derives_its_vocabulary_instead_of_copying_it():
    """出域契约的成因与动作都**派生**自词表 —— 值集恒等靠派生，不靠两处抄写。

    与 `AdmissionReasonLiteral` 同一个做法：契约层一旦要 import 服务层的词表，那个
    模块就不许 import 任何项目模块（否则会把 `vision_encoder` 拖进契约层的导入路径）。
    """
    schema = (_REPO / "app" / "schema" / "report.py").read_text(encoding="utf-8")
    assert "from app.service.interpretation_failure import" in schema
    assert "FAILURE_LITERAL" in schema
    assert "PATIENT_ACTION_LITERAL" in schema
    for cause in FAILURES_ORDERED:
        assert f'"{cause}"' not in schema, f"{cause} 被抄进了契约 —— 应当派生"


def test_a_reports_response_block_comes_from_what_the_queue_recorded():
    """端到端：解析交出的成因落到库里，`GET /report/{id}` 把它读成失败块。

    这一条把两半接起来 —— 第一张票的解析路径写下 `parsed_content["failure"]`，这一张票
    把它出域。两半各自通过、接起来却是空的时候，这条会红。
    """
    from app.api.report import _report_response

    factory, report_id = _report_with(
        {
            "failure": "content_unrecognized",
            "metric_count": 0,
            "file_results": [{"file_index": 1, "filename": "体检单.png", "status": "failed"}],
        }
    )
    with factory() as db:
        payload = _report_response(db.get(ReportModel, report_id), []).model_dump()

    assert payload["interpretation_failure"] == {
        "cause": "content_unrecognized",
        "retryable": False,
        "action": "upload_again",
        "failed_files": [{"file_index": 1, "filename": "体检单.png"}],
    }


def test_the_gate_says_the_cause_instead_of_a_generic_re_upload_line():
    """闸门被拒时说的话由成因派生 —— 「服务当时不可用」不许说「请重新上传」。"""
    from app.api.report import _gate_detail

    factory, report_id = _report_with({"failure": "provider_unavailable"}, status="processing")
    with factory() as db:
        detail = _gate_detail(db.get(ReportModel, report_id))

    assert detail, "有成因时闸门必须说点什么"
    assert "重新上传" not in detail, "我们这边出的问题不该叫患者重传"

    factory2, report_id2 = _report_with({"failure": "material_unreadable"}, status="processing")
    with factory2() as db:
        assert "重新上传" in _gate_detail(db.get(ReportModel, report_id2))


def test_every_cause_has_a_gate_sentence_and_the_two_causes_agree_with_the_action():
    """词表与闸门话术不许漂：每个成因都要有话说，且「重传」只在两个成因下出现。"""
    from app.service.interpretation_failure import GATE_DETAIL_BY_FAILURE, gate_detail

    assert set(GATE_DETAIL_BY_FAILURE) == set(FAILURES_ORDERED)
    for cause in FAILURES_ORDERED:
        mentions_upload = "重新上传" in gate_detail(cause) or "重传" in gate_detail(cause)
        assert mentions_upload is (PATIENT_ACTION_BY_FAILURE[cause] == "upload_again"), cause


def test_no_gate_sentence_promises_an_automatic_retry():
    """终态的失败不许承诺「会自动重试」。

    闸门在**两种**时刻被拒：报告还在 `processing`（重试中）与已经是 `failed`（次数
    用完了）。同一句话不能对两者都成立 —— 所以它不承诺。这一条与前端那句「不承诺」
    是同一条边界，两侧各守一次：服务端的话出现在 HTTP 响应里，前端的话在卡片上。
    """
    from app.service.interpretation_failure import GATE_DETAIL_BY_FAILURE

    for cause, detail in GATE_DETAIL_BY_FAILURE.items():
        for banned in ("自动重试", "会自动", "马上重试", "正在重试"):
            assert banned not in detail, f"{cause} 的闸门话术承诺了 {banned}：{detail}"


# ── 陈旧/畸形数据的读路径（评审发现的三条） ────────────────────────────────────


@pytest.mark.parametrize("malformed", [["a"], {"x": 1}, 7, True])
def test_a_malformed_stored_cause_does_not_block_the_report(malformed):
    """`parsed_content` 是 JSON 列，历史行里这个键可能是任何形状。

    一个列表或字典直接拿去查集合会抛 `TypeError: unhashable type` —— 那会让整份报告的
    读路径 500，患者连打开都做不到，而这只是一个我们读不懂的陈旧字段。
    """
    factory, report_id = _report_with({"failure": malformed, "file_results": []})
    assert _block(factory, report_id) is None

    from app.api.report import _gate_detail

    with factory() as db:
        assert _gate_detail(db.get(ReportModel, report_id)) is None


def test_a_new_failure_does_not_blame_the_previous_round_s_files():
    """一次部分解析留下 `file_results` 之后，**下一轮**整份材料因服务不可用而失败时，
    那份旧结果还在 —— 在新的失败块里「坏.png」会被列成这一次失败的文件。

    患者看到的是一次服务故障去归咎于某个文件。逐轮结果必须丢掉。
    """
    from app.api.report import _carried_forward

    previous = {
        "file_results": [{"file_index": 2, "filename": "坏.png", "status": "failed"}],
        "failure": "provider_unavailable",
        "raw_text": "上一轮的原文",
        "extraction_provider": "openai-compatible-chat",
        "warnings": ["坏.png: provider timeout"],
    }
    carried = _carried_forward(previous)

    assert "file_results" not in carried, "上一轮的文件结果不许活到这一轮"
    assert "failure" not in carried
    assert "warnings" not in carried
    # 抽取轨迹是**历史事实**，留下：下一轮可能什么都还没产生。
    assert carried["raw_text"] == "上一轮的原文"
    assert carried["extraction_provider"] == "openai-compatible-chat"
