"""解读准入的逐行出域与患者侧文案（#184）。

两份出域形状各有一条断言，因为它们回答的是**两个不同的问题**：

- 逐行 `admission_reason`：这一行为什么没进解读（`None` = 服务端没拦下它）；
- 报告级 `admission` 台账：这份报告里各类各有多少行。

台账不是「逐行字段的和」的另一种写法 —— 逐行那个 `None` 兼了「进入解读」与「还没评估」
两件事，而台账为空时才是「还没有结论」。这一条是本票最容易做错的地方，所以它单独有一
条断言（`test_the_ledger_is_absent_before_the_report_is_assessed`）。

前端的部分由 esbuild 驱动**真实模块**（先例：`tests/test_frontend_page_count.py`），
断言的是「三种不同的原因得到三句不同的话」，而不是文案本身 —— 文案会改，口径不该。
"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

from app.service.admission_projection import admission_shapes, ledger, metric_reasons
from app.service.admission_vocabulary import vocabulary

REPO_ROOT = Path(__file__).resolve().parents[1]
NODE = shutil.which("node")
ADMISSION_MODULE = REPO_ROOT / "frontend" / "src" / "admissionText.js"


def _row(**overrides):
    from app.data.models import MetricRecord as MetricModel

    values = {
        "id": 1,
        "report_id": 1,
        "metric_name": "空腹血糖",
        "metric_value": "6.5",
        "unit": "mmol/L",
        "reference_range": "3.9-6.1",
        "page_number": 1,
        "evidence_text": "空腹血糖 6.5 mmol/L (3.9-6.1)",
        "source_file_index": 1,
        "confirmation_status": "confirmed",
        "metric_code": "fasting_glucose",
    }
    values.update(overrides)
    return MetricModel(**values)


# ── 逐行结论 ────────────────────────────────────────────────────────────────


def test_every_parsed_row_can_be_mapped_to_exactly_one_conclusion():
    """全量性：每一行都有且只有一个准入结论，且四类相加 == 行数。

    这份 PRD 的硬指标。此前 `pending` / `excluded` 落在三桶之外、由前端从原始
    `confirmation_status` 猜，所以这个等式永远成立不了。
    """
    rows = [
        _row(id=1),  # 进入解读
        _row(id=2, metric_value="5.0"),  # 在参考区间内
        _row(id=3, unit=None, evidence_text=None),  # 缺单位
        _row(id=4, metric_value=""),  # 值还没解析出来
        _row(id=5, metric_code=None),  # 没有可对应的编码
        _row(id=6, confirmation_status="pending"),
        _row(id=7, confirmation_status="excluded"),
    ]
    reasons = metric_reasons(rows)
    counts = ledger(reasons)

    assert counts.total == len(rows)
    assert counts.total == counts.included + counts.skipped + counts.unmatched + counts.not_evaluated
    assert (counts.included, counts.skipped, counts.unmatched, counts.not_evaluated) == (1, 3, 1, 2)


def test_a_row_shown_as_abnormal_but_kept_out_of_the_reading_now_carries_a_reason():
    """「报告单说 H、历史摘要计入异常、却因缺单位没进解读」的行，不再无解释。

    这是本票要消灭的患者可见分叉：`inferred_abnormal_flag` 只看值与参考范围，准入还看
    证据完备性。分叉**保留**（它是有意的），但原因必须说得出来。
    """
    metric = _row(unit=None, evidence_text=None, metric_value="7.5")
    reasons = metric_reasons([metric])
    assert reasons == ["missing_unit"]
    # 异常判定照旧给 H —— 本票不动它，历史摘要计数因此不变。
    from app.service.evidence_bridge import infer_abnormal_flag_for_metric

    assert infer_abnormal_flag_for_metric(metric) == "H"


def test_rows_that_have_not_been_assessed_carry_no_conclusion():
    """还没评估的报告不写结论。

    写一个 `pending` 会把「患者还没核对那一行」与「整份报告还没评估」混成同一个词 ——
    后者是**报告级**事实，不该逐行重复。
    """
    rows = [_row(id=1), _row(id=2, metric_value="5.0")]
    pairs, ledger_value = admission_shapes(rows, assessed=False)
    assert [reason for _, reason in pairs] == [None, None]
    assert ledger_value is None


def test_the_ledger_is_absent_before_the_report_is_assessed():
    """台账在未评估时为空 —— 不用「四类全 0」表达「还没有结论」。

    全 0 与「没有一行被拦下」在数值上一模一样。区分它们若靠旁证（例如一个时间戳），
    就等于给同一个问题留第二个答案 —— 报告状态才是那个答案的唯一来源。
    """
    rows = [_row(id=1)]
    _, ledger_value = admission_shapes(rows, assessed=True)
    assert ledger_value is not None
    assert ledger_value.included == 1

    _, absent = admission_shapes(rows, assessed=False)
    assert absent is None


def test_the_ledger_has_no_field_other_than_the_counts():
    """台账只描述行数，不带时间戳或别的旁证。"""
    from app.schema.report import AdmissionLedger

    assert set(AdmissionLedger.model_fields) == {"included", "skipped", "unmatched", "not_evaluated", "total"}


# ── 响应级：两条形状都真的出域 ──────────────────────────────────────────────


def _assessed_report(**metric_overrides):
    import tests.test_report_confirmation as T
    from app.api.report import _assess_report, _ordered_metrics, _report_response

    session, report = T._assessment_fixture(**metric_overrides)
    with patch("app.api.report.match_published_evidence", return_value=T._evidence_result()):
        asyncio.run(_assess_report(report, session))
    session.refresh(report)
    metrics = _ordered_metrics(session, report.id).all()
    return session, _report_response(report, metrics)


def test_an_assessed_report_carries_both_shapes():
    session, response = _assessed_report(metric_code="fasting_glucose", metric_value="6.5")
    try:
        assert response.status == "assessed"
        assert response.admission is not None
        assert response.admission.total == len(response.metrics)
        # 逐行：每一条都能映射到一个结论（`None` 也算「进入解读」）。
        for metric in response.metrics:
            assert metric.admission_reason is None or metric.admission_reason in vocabulary()
    finally:
        session.close()


def test_a_confirmed_report_carries_neither_conclusion_nor_ledger():
    """还没评估的报告：逐行无结论，报告级无台账。"""
    import tests.test_report_confirmation as T
    from app.api.report import _ordered_metrics, _report_response

    session, report = T._assessment_fixture(metric_code="fasting_glucose", metric_value="6.5")
    try:
        assert report.status == "confirmed"
        response = _report_response(report, _ordered_metrics(session, report.id).all())
        assert response.admission is None
        assert all(metric.admission_reason is None for metric in response.metrics)
    finally:
        session.close()


def test_the_ledger_and_the_skipped_array_agree_on_what_was_skipped():
    """台账的 `skipped` 计数与 `skipped` 数组长度一致 —— 两份形状同源。"""
    session, response = _assessed_report(metric_code="fasting_glucose", metric_value="6.5")
    try:
        skipped = response.evidence_result.skipped or []
        assert response.admission.skipped == len(skipped)
    finally:
        session.close()


# ── 前端：按原因渲染，不再自己推导 ──────────────────────────────────────────

_DRIVER = """
import fs from 'node:fs';
import path from 'node:path';
import process from 'node:process';
import { pathToFileURL } from 'node:url';
import { build } from 'esbuild';

const entry = process.env.HEALTHFLOW_TEST_MODULE;
const outDir = process.env.HEALTHFLOW_TEST_OUTDIR;
const outfile = path.join(outDir, 'admission.mjs');
await build({ entryPoints: [entry], outfile, bundle: true, format: 'esm', logLevel: 'silent' });
const mod = await import(pathToFileURL(outfile).href);
const cases = JSON.parse(process.env.HEALTHFLOW_TEST_CASES);
fs.writeFileSync(path.join(outDir, 'result.json'), JSON.stringify({
  texts: cases.reasons.map((reason) => mod.admissionText(reason)),
  groups: mod.admissionGroups(cases.metrics),
  notable: cases.reasons.map((reason) => mod.notableAdmission(reason)),
}));
"""


def _drive_frontend(payload: dict) -> dict:
    frontend = REPO_ROOT / "frontend"
    work = frontend / "node_modules" / f".tmp-admission-{os.getpid()}"
    shutil.rmtree(work, ignore_errors=True)
    work.mkdir(parents=True)
    try:
        driver = work / "driver.mjs"
        driver.write_text(_DRIVER, encoding="utf-8")
        proc = subprocess.run(
            [NODE, str(driver)],
            capture_output=True,
            text=True,
            env={
                **os.environ,
                "HEALTHFLOW_TEST_MODULE": str(ADMISSION_MODULE),
                "HEALTHFLOW_TEST_OUTDIR": str(work),
                "HEALTHFLOW_TEST_CASES": json.dumps(payload),
            },
            cwd=frontend,
            timeout=180,
        )
        assert proc.returncode == 0, f"驱动失败:\n{proc.stdout}\n{proc.stderr}"
        return json.loads((work / "result.json").read_text(encoding="utf-8"))
    finally:
        shutil.rmtree(work, ignore_errors=True)


needs_node = [
    pytest.mark.skipif(NODE is None, reason="需要 node 才能驱动前端模块"),
    pytest.mark.skipif(
        not (REPO_ROOT / "frontend" / "node_modules" / "esbuild").is_dir(),
        reason="需要 frontend/node_modules（esbuild）；先在前端目录 npm install",
    ),
]


@needs_node[0]
@needs_node[1]
def test_three_different_reasons_render_three_different_sentences():
    """「值还没解析出来」「值不是一个数」「缺少参考范围」必须是三句不同的话。

    它们要求患者做三件不同的事：去等解析、去修正数值、去核对参考范围。压成一句
    「数值无法识别为单个数字」（旧行为）会把人引向错误的动作 —— 尤其是第三种，
    它的值本身完全正常。
    """
    result = _drive_frontend(
        {"reasons": ["missing_value", "invalid_value", "missing_reference_range"], "metrics": []}
    )
    texts = result["texts"]
    assert len(set(texts)) == 3, texts
    assert all(isinstance(text, str) and text for text in texts)


@needs_node[0]
@needs_node[1]
def test_within_reference_range_is_never_worded_as_not_read():
    """「在参考区间内」就是「正常」，不许被说成「未进入解读」。

    服务端在同一张卡片上方刚说过「均在参考区间内」，而旧的前端在下方又把同一批行
    说成「未进入匹配」—— 两侧口径分叉的直接来源。
    """
    result = _drive_frontend(
        {"reasons": ["within_reference_range"], "metrics": [{"admission_reason": "within_reference_range"}]}
    )
    assert result["notable"] == [False], "「在参考区间内」不该被当成一条待说明的结论"
    assert result["groups"] == [], "它也不该出现在逐条说明里"
    # 而且它自己的那句话里不许出现「未进入匹配」这类说法 —— 它是「正常」。
    text = result["texts"][0]
    assert "未进入匹配" not in text and "未进入解读" not in text, text


@needs_node[0]
@needs_node[1]
def test_groups_are_grouped_by_reason_and_never_mention_not_read_for_normal_rows():
    result = _drive_frontend(
        {
            "reasons": [],
            "metrics": [
                {"admission_reason": "missing_reference_range"},
                {"admission_reason": "missing_reference_range"},
                {"admission_reason": "missing_value"},
                {"admission_reason": "within_reference_range"},
            ],
        }
    )
    groups = result["groups"]
    assert [group["reason"] for group in groups] == ["missing_value", "missing_reference_range"]
    assert {group["count"] for group in groups} == {1, 2}
    assert all("未进入匹配" not in group["text"] for group in groups)
    assert all(group["reason"] != "within_reference_range" for group in groups)


@needs_node[0]
@needs_node[1]
def test_an_unknown_reason_falls_back_instead_of_guessing():
    """服务端加了新原因而前端还没跟上时：说「未进入解读」，不猜一个更具体的意思。

    猜错会让人去修正一个没有毛病的东西 —— 这正是本票要修的那类误导。
    """
    result = _drive_frontend({"reasons": ["a_reason_from_the_future"], "metrics": []})
    text = result["texts"][0]
    assert "未进入解读" in text
    assert "数值" not in text and "参考范围" not in text


# ── 静态守卫：前端不再自己推导 ──────────────────────────────────────────────


def test_the_frontend_no_longer_derives_pending_or_excluded():
    """前端不再从 `confirmation_status` 推出「待核对 / 已排除」。

    那两句话现在由服务端的准入结论给出。守卫查的是**原始状态字段被读去推导展示状态**
    这一件事 —— `confirmation_status` 仍可作为表单初值与请求载荷，所以只禁止它与
    展示分支出现在同一处。
    """
    source = (REPO_ROOT / "frontend" / "src" / "pages" / "Upload.jsx").read_text(encoding="utf-8")
    code = "\n".join(line.split("//", 1)[0] for line in source.splitlines())
    offenders = [
        line.strip()
        for line in code.splitlines()
        if "confirmation_status" in line and ("=== 'excluded'" in line or "=== 'pending'" in line)
    ]
    assert not offenders, "展示状态不得从原始 confirmation_status 推导：" + "; ".join(offenders)


def test_the_summary_card_is_driven_by_the_server_ledger():
    """卡片底部读报告级台账，不再读 `skipped` 数组的长度。

    `skipped` 里没有「尚未核对」与「患者已排除」两类 —— 而它们同样需要一句说明。
    读长度会把它们整类漏掉，也会把 `within_reference_range`（正常）一并算进去。
    """
    source = (REPO_ROOT / "frontend" / "src" / "pages" / "Upload.jsx").read_text(encoding="utf-8")
    code = "\n".join(line.split("//", 1)[0] for line in source.splitlines())
    assert "skipped.length > 0 && (" not in code, "底部说明不得再以 skipped 数组为准"
    assert "admissionLines(ledger)" in code, "底部说明应从报告级台账生成"


def test_the_frontend_no_longer_infers_value_usability_from_the_flag():
    """`valueUnusable`（拿空判定当「值用不了」的代理）必须消失。

    它把四种事实压成一句，其中「缺少参考范围」的值本身完全正常。
    """
    source = (REPO_ROOT / "frontend" / "src" / "pages" / "Upload.jsx").read_text(encoding="utf-8")
    # **先剥注释**：解释「为什么不再这么说」的注释里正写着那几句原话，不剥的话这条
    # 守卫会因为自己的说明而红。断言查的是**代码**里不再出现它们。
    code = "\n".join(line.split("//", 1)[0] for line in source.splitlines())
    assert "valueUnusable" not in code
    assert "数值无法识别为单个数字" not in code
    # 旧的合并句同样不许回来。
    assert "未进入匹配（正常" not in code
