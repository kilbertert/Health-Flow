"""解读失败的静态守卫与解析路径行为（PRD #226 第一张票）。

两件事：**子串嗅探已退场**（它是最容易被悄悄长回来的东西），以及解析路径里的
基础设施故障不再对患者表现为「这份报告读不出来」。
"""

import re
from pathlib import Path
from unittest.mock import patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.data.models import Base
from app.data.models import MedicalReport as ReportModel
from app.service.interpretation_failure import FAILURES_ORDERED, classify

_REPO = Path(__file__).resolve().parents[1]
_APP = _REPO / "app"
_DOCSTRING = re.compile(r'""".*?"""|\'\'\'.*?\'\'\'', re.DOTALL)


def _python_sources():
    return [path for path in _APP.rglob("*.py") if "__pycache__" not in path.parts]


def _code_only(source: str) -> str:
    """去掉注释与文档字符串后的源码。

    注释与文档字符串里**必须**能写下「这里以前有个子串表」—— 那是删掉它的理由，
    也是下一个读者不会把它加回来的唯一依据。守卫拦的是**代码**里再出现它。
    """
    without_docstrings = _DOCSTRING.sub("", source)
    return "\n".join(line.split("#", 1)[0] for line in without_docstrings.splitlines())


def test_the_filename_and_free_text_sniffer_is_gone():
    """**这是本 PRD 的静态锚。** 删掉它之后，重试决策会从别处长回来 —— 错误文本还在、
    `error_class` 还在。所以这里拦的是「代码里没有第二处判定」。
    """
    offenders = []
    for path in _python_sources():
        code = _code_only(path.read_text(encoding="utf-8"))
        if "_TRANSIENT_ERROR_MARKERS" in code or "_retryable_error" in code:
            offenders.append(path.relative_to(_REPO).as_posix())
    assert offenders == [], "重试判定的子串嗅探又出现了：" + ", ".join(offenders)


def test_the_guard_can_fail():
    """守卫自己要能被弄红 —— 否则它只是又一次「看起来在检查」。"""
    planted = 'def _retryable_error(text):\n    return "timeout" in text\n'
    assert "_retryable_error" in _code_only(planted)
    assert "_TRANSIENT_ERROR_MARKERS" in _code_only("_TRANSIENT_ERROR_MARKERS = ('timeout',)")
    assert "_TRANSIENT_ERROR_MARKERS" not in _code_only('"""_TRANSIENT_ERROR_MARKERS 已经退场。"""')


def test_the_vocabulary_is_declared_in_exactly_one_module():
    """一份成因词表。契约层要 import 它，所以它不得把 `vision_encoder` 拖进导入路径。"""
    declaring = [
        path.relative_to(_REPO).as_posix()
        for path in _python_sources()
        if re.search(r"^FAILURES_ORDERED\s*:", path.read_text(encoding="utf-8"), re.MULTILINE)
    ]
    assert declaring == ["app/service/interpretation_failure.py"]


def test_the_vocabulary_module_imports_no_project_module():
    """词表之家**只 import 标准库**。

    `app.schema.report` import 它，而 `app/service/__init__.py` 的 docstring 记着那条
    实测教训：`app.service.<任何子模块>` 曾把 `vision_encoder` 拉起来，而
    `vision_encoder` 反过来 import `app.schema.report` —— 契约层一旦 import 服务层就成环。
    """
    source = (_APP / "service" / "interpretation_failure.py").read_text(encoding="utf-8")
    project_imports = re.findall(r"^from app[.\w]*|^import app[.\w]*", source, re.MULTILINE)
    assert project_imports == []


def test_an_infrastructure_failure_while_parsing_is_not_a_content_failure():
    """解析期抛出的**服务端**异常（数据库、目录）落成可重试的 `service_unavailable`。

    今天这条会落 `failed` 并给患者一句「请重新上传或稍后重试」——而那份报告和它的
    输入一点问题都没有。ADR-0006 那次线上事故的形状就是这个。
    """
    from app.api.report import _parse_report

    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    session_factory = sessionmaker(bind=engine)
    with session_factory() as seed:
        report = ReportModel(patient_id="P-infra", status="processing", subject_consistency="same")
        seed.add(report)
        seed.commit()
        report_id = report.id

    def boom():
        raise RuntimeError("database is gone")

    with patch("app.api.report.get_vision_encoder_service", side_effect=boom):
        outcome = _parse_report(report_id, [(1, "a.png", "image/png", b"x")], session_factory)

    assert outcome.succeeded is False
    assert outcome.failure in FAILURES_ORDERED
    assert outcome.failure == "service_unavailable"
    with session_factory() as check:
        report = check.get(ReportModel, report_id)
        assert report.parsed_content["failure"] == "service_unavailable"
        # 还留在 processing：队列才是「还要不要再跑一轮」的判定者，报告此时不该给患者
        # 看一眼已经注定的失败。
        assert report.status == "processing"


def test_a_layer_decides_by_position_not_by_the_text_it_carries():
    """`material`/`service` 两环的结论由**位置**决定，与错误文本无关。"""
    assert classify("material", RuntimeError("database is gone")) == "material_unreadable"
    assert classify("service", RuntimeError("database is gone")) == "service_unavailable"


def test_a_database_outage_on_the_failure_path_does_not_escape_the_parse_path():
    """**记事实这一步自己也会失败。** 数据库都没了，`db.get` 会原样再抛一次 —— 让那个
    异常逃出去会从 `run_next_job` 里炸掉整个 worker 循环，后面每一份报告都不再被处理：
    一次数据库抖动变成一条永久停摆的队列。
    """
    from app.api.report import _parse_report

    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    session_factory = sessionmaker(bind=engine)
    with session_factory() as seed:
        report = ReportModel(patient_id="P-outage", status="processing", subject_consistency="same")
        seed.add(report)
        seed.commit()
        report_id = report.id

    def _broken_factory():
        """每次开 session 都炸 —— 模拟数据库整体不可用。"""
        raise RuntimeError("database is gone")

    with patch("app.api.report.get_vision_encoder_service", side_effect=RuntimeError("database is gone")):
        outcome = _parse_report(report_id, [(1, "a.png", "image/png", b"x")], _broken_factory)

    # 结论仍然交得出去（成因是从异常本身得出的，不依赖落库）。
    assert outcome.succeeded is False
    assert outcome.failure == "service_unavailable"


def test_a_rejection_only_visible_in_the_message_text_is_not_retried():
    """`Error code: 401 - ...` 这种 provider 错误把状态码只印在**消息**里 —— 拿不到
    异常属性上的结构化事实时，文本是唯一剩下的判据，而它必须被用上。

    漏掉这一条会让一次**凭据过期**被判成 `provider_unavailable`（可重试），于是同一份
    无效凭据被重发 `REPORT_JOB_MAX_ATTEMPTS` 次 —— ADR-0006 明确否决过这件事。
    """
    exc = RuntimeError("Error code: 401 - Invalid 'Authorization' header or token.")
    assert classify("provider", exc, text=str(exc)) == "provider_rejected"
    # 调用点必须把文本一起交出去，否则上面这条判据永远不会被用到。
    source = (_APP / "service" / "vision_encoder.py").read_text(encoding="utf-8")
    assert 'classify("provider", exc, text=str(exc))' in source, (
        "provider 异常的分类必须带上 str(exc)：只把状态码印在消息里的错误只认文本"
    )
