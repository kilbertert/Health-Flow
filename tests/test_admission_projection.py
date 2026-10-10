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
        "source_file_index": 1,
        "confirmation_status": "confirmed",
        "metric_code": "fasting_glucose",
    }
    values.update(overrides)
    # 原文证据里必须**真的含这个值**：门禁要求如此，手写一个与值不符的字符串会让几乎
    # 每个用例都以 `missing_source_evidence` 失败 —— 那是测试自己造的假象。
    if "evidence_text" not in overrides:
        values["evidence_text"] = f"空腹血糖 {values['metric_value']} mmol/L ({values['reference_range']})"
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
    assert counts.total == (
        counts.included + counts.normal + counts.skipped + counts.unmatched + counts.not_evaluated
    )
    # 进入解读 1 / normal 1（在参考区间内）/ skipped 2（缺单位、值还没解析出来）/
    # unmatched 1 / 未评估 2。
    assert (counts.included, counts.normal, counts.skipped, counts.unmatched, counts.not_evaluated) == (
        1,
        1,
        2,
        1,
        2,
    )


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


def test_a_normal_heavy_report_does_not_claim_rows_failed_to_be_read():
    """一份「大部分正常」的报告，台账里的「未进入解读」不许把正常指标算进去。

    这是患者可见的缺陷（评审指出）：`within_reference_range` 的语义是「判定过，在参考
    区间内」—— **正常**。它若并入 `skipped`，页面会一边说「均在参考区间内」、一边说
    「有 N 项未进入解读」，而 N 里大半是正常指标 —— 正是本次改动要消灭的那种自相矛盾。
    """
    rows = [
        _row(id=1, metric_value="5.0"),  # 正常
        _row(id=2, metric_value="5.2"),  # 正常
        _row(id=3, metric_value="5.4"),  # 正常
        _row(id=4, unit=None, evidence_text=None),  # 缺单位 —— 真的没进解读
        _row(id=5),  # 进入解读
    ]
    counts = ledger(metric_reasons(rows))

    assert counts.normal == 3, "三条正常指标要单独成一桶"
    assert counts.skipped == 1, "只有真的没进解读的那一条算 skipped"
    assert counts.skipped + counts.unmatched == 1, "页面那句「N 项未进入解读」的分子"
    assert counts.total == 5


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

    assert set(AdmissionLedger.model_fields) == {
        "included",
        "normal",
        "skipped",
        "unmatched",
        "not_evaluated",
        "total",
    }


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


def test_the_ledger_and_the_skipped_array_agree_when_the_catalog_re_resolves_a_row():
    """目录在确认时不可用、评估时恢复：台账与被实际处理的那一行必须同口径。

    这是两份形状最容易被做成分叉的地方 —— 门禁会用权威目录再裁决一次编码，而逐行
    投影若只看落定的编码，那一行会一边被送进匹配、一边显示「没有可对应的编码」。台账
    的 `unmatched` 会变成一个既不等于 `unmatched` 数组、也不等于 `skipped` 数组的
    **第三个数**，读的人无法判断该信哪个。
    """
    import tests.test_report_confirmation as T
    from app.api.report import _assess_report, _ordered_metrics, _report_response

    # 确认那刻目录不可用 → 落定编码为空；评估时目录可用（catalog 由 fetch 提供）。
    session, report = T._assessment_fixture(
        metric_code=None, metric_name="空腹血糖", metric_value="6.5", evidence_text="空腹血糖 6.5 mmol/L 3.9-6.1"
    )
    with patch("app.api.report.match_published_evidence", return_value=T._evidence_result()):
        asyncio.run(_assess_report(report, session))
    session.refresh(report)
    metrics = _ordered_metrics(session, report.id).all()

    catalog = ["fasting_glucose"]
    with_catalog = _report_response(report, metrics, catalog=catalog)
    without_catalog = _report_response(report, metrics)
    try:
        # 目录可用：名称能解析出编码 → 这一行没有「没有编码」这个结论。
        assert all(m.admission_reason != "unknown_metric_code" for m in with_catalog.metrics)
        assert with_catalog.admission.unmatched == 0
        # 目录不可用：不猜 —— 与门禁同一条规矩。
        assert any(m.admission_reason == "unknown_metric_code" for m in without_catalog.metrics)
        assert without_catalog.admission.unmatched == 1
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
  notParsed: (cases.notParsed || []).map((m) => mod.valueNotParsed(m)),
  valueReasons: (cases.notParsed || []).map((m) => mod.valueReasonOrNull(m)),
  missingTexts: mod.missingTexts(cases.vocabulary || []),
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
@needs_node[0]
@needs_node[1]
def test_every_reason_in_the_server_vocabulary_has_a_sentence():
    """词表里的每一个原因都必须有一句话。

    少一个就会在界面上显示「原因未识别」—— 那不是兜底的兜底，是漏配。这条守卫让
    「服务端加了新原因」变成一个**会红的测试**，而不是一句没人看见的文案。
    """
    result = _drive_frontend({"reasons": [], "metrics": [], "vocabulary": sorted(vocabulary())})
    assert result["missingTexts"] == [], f"这些原因在 ADMISSION_TEXT 里没有对应文案：{result['missingTexts']}"


def test_an_unknown_reason_falls_back_instead_of_guessing():
    """服务端加了新原因而前端还没跟上时：说「未进入解读」，不猜一个更具体的意思。

    猜错会让人去修正一个没有毛病的东西 —— 这正是本票要修的那类误导。
    """
    result = _drive_frontend({"reasons": ["a_reason_from_the_future"], "metrics": []})
    text = result["texts"][0]
    assert "未进入解读" in text
    assert "数值" not in text and "参考范围" not in text


@needs_node[0]
@needs_node[1]
def test_a_row_whose_value_cannot_be_parsed_cannot_default_to_confirmed():
    """#129 的守卫：值解析不出一个数的行，默认**不能**是「确认」。

    这一条必须在**评估之前**就生效，而那时服务端的准入结论还没有值（逐行
    `admission_reason` 全是 `null`）—— 所以它只能由「值这一类」的服务端名字来判。
    本票的改动一度丢掉了它（改成只看 `admission_reason`），这条断言就是那次回归的
    护栏：它同时覆盖两个时机。
    """
    result = _drive_frontend(
        {
            "reasons": [],
            "metrics": [],
            "notParsed": [
                # 评估之后：服务端说了名字 —— 多值（invalid_value）与还没解析（missing_value）。
                {"admission_reason": "invalid_value", "abnormal_flag": "H"},
                {"admission_reason": "missing_value", "abnormal_flag": "H"},
                # 参考范围缺失**不**在此列：值本身正常，让人去修正数字是误导。
                {"admission_reason": "missing_reference_range", "abnormal_flag": "H"},
                # 正常行。
                {"admission_reason": "within_reference_range", "abnormal_flag": "N"},
                # 评估之前（准入结论为 null）：多值异常仍必须被拦下。
                {"admission_reason": None, "abnormal_flag": "H", "metric_value": "3.87 4.00",
                 "inferred_abnormal_flag": None},
                # 评估之前、可解析的异常：不该被拦（否则患者被挡在一个完全能确认的指标前）。
                {"admission_reason": None, "abnormal_flag": "H", "metric_value": "6.9",
                 "inferred_abnormal_flag": "H"},
                # 旧响应（字段缺失）：无从判断，保守不拦。
                {"admission_reason": None, "abnormal_flag": "H", "metric_value": "3.87 4.00"},
            ],
        }
    )
    assert result["notParsed"] == [True, True, False, False, True, False, False], result["notParsed"]
    # 「还没解析出来」与「不是一个数」在这一侧也要分开 —— 患者要做的动作不同。
    assert result["valueReasons"] == [
        "invalid_value",
        "missing_value",
        None,
        None,
        "invalid_value",
        None,
        None,
    ], result["valueReasons"]


# ── 静态守卫：前端不再自己推导 ──────────────────────────────────────────────


def test_the_frontend_never_derives_a_status_outside_the_named_helpers():
    """`confirmation_status` 的读取集中在**两个有名字的**函数里，展示分支不各读一次。

    服务端的准入结论是「这条指标进没进解读」的答案。前端仍要在这件事上读原始状态，但只有
    两处、各有分工（`#191` 把它们分开之后）：

    - `admissionText.admissionBeforeAssessment` —— **评估之前**显示患者自己的表态
      （排除 / 尚未核对）。那是同一个概念、同一个来源，所以它集中在一个函数里并写明理由。
    - `Upload.serverDecision` —— 重入确认时「服务端已经就这一行给过的决策」，用作提交
      载荷的候选值。它**不是**展示。

    这条守卫禁止的是**第三个读者**：展示分支里各读一次原始状态。
    """
    upload = (REPO_ROOT / "frontend" / "src" / "pages" / "Upload.jsx").read_text(encoding="utf-8")
    code = "\n".join(line.split("//", 1)[0] for line in upload.splitlines())
    reads = [line.strip() for line in code.splitlines() if "confirmation_status" in line]
    assert reads == ["const status = metric?.confirmation_status;"], (
        "展示层只允许 `serverDecision` 一个读取点；现在这些地方也在读：" + "; ".join(reads)
    )

    module = (REPO_ROOT / "frontend" / "src" / "admissionText.js").read_text(encoding="utf-8")
    body = module[module.index("export function admissionBeforeAssessment") :]
    body = body[: body.index("\n}")]
    comparisons = [line.strip() for line in body.splitlines() if "confirmation_status" in line]
    assert comparisons, "这个函数必须真的读 confirmation_status（否则守卫的锚点失效）"
    # 每一处读取都必须是在**比对两个具体取值**，不是把原值原样用出去。
    assert all("status ===" in line for line in comparisons), comparisons


def _jsx_call_sites(source: str, component: str) -> list[tuple[int, str]]:
    """`<Component ... />` 的每一处调用：`(行号, 那段文本)`。"""
    found: list[tuple[int, str]] = []
    start = 0
    while (index := source.find(f"<{component}", start)) != -1:
        end = source.index("/>", index) + 2
        found.append((source[:index].count("\n") + 1, source[index:end]))
        start = end
    return found


def test_every_page_that_renders_the_summary_card_passes_it_the_ledger():
    """报告详情页与解读页共用同一个卡片，两处都必须把台账传进去。

    漏传不会报错 —— 卡片对 `null` 台账是静默的（那是「还没有结论」的合法表示）。于是
    同一份报告在解读页会看到「有 N 项未进入解读」，在详情页不会，而两处说的是同一件事。
    这条守卫按**调用点**查：任何 `<EvidenceResult ...>` 都必须带 `admissionLedger`。
    """
    offenders: list[str] = []
    for path in sorted((REPO_ROOT / "frontend" / "src").rglob("*.jsx")):
        for line, call in _jsx_call_sites(path.read_text(encoding="utf-8"), "EvidenceResult"):
            if "admissionLedger" not in call:
                offenders.append(f"{path.relative_to(REPO_ROOT)}:{line} {call[:70]}")
    assert offenders == [], "这些调用没有把报告级台账传进卡片：" + "; ".join(offenders)


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
