"""报告指标计数是单点，且结论句不与计数打架（GLOSSARY.md 的「报告指标计数」，#245）。

本文件钉住三件事：

1. **四个数各有唯一判据、由一处给出** —— 历史摘要、报告单、确认页读的是同一份。
2. **两个谓词不同源的那条负控**：一行 `raw=H` 而值判不出来时，它计入「需要处理」而
   **不计入**「异常」，且 `undecided` 为真 —— 这正是改动前「报告单说未见异常指标、
   确认页说 N 项需要确认」的那条可达的行。
3. **结论句的前提**：有未判定的行时，报告单不许宣告「均在参考区间内」。这是本次改动
   的可见行为变化。
"""

from __future__ import annotations

import ast
import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from app.service.report_metric_counts import MetricCounts, metric_counts, needs_review, undecided

REPO_ROOT = Path(__file__).resolve().parents[1]


class _Row:
    """一条指标行的最小替身：计数只读这几个字段。"""

    def __init__(
        self,
        *,
        value: str,
        reference: str | None = "3.9-6.1",
        unit: str | None = "mmol/L",
        raw_flag: str | None = None,
        status: str = "pending",
        evidence: str | None = None,
        page: int | None = 1,
    ) -> None:
        self.metric_value = value
        self.reference_range = reference
        self.unit = unit
        self.abnormal_flag = raw_flag
        self.confirmation_status = status
        self.evidence_text = evidence if evidence is not None else f"空腹血糖 {value} mmol/L"
        self.page_number = page
        self.metric_name = "空腹血糖"
        self.confirmed_value = None
        self.confirmed_unit = None
        self.confirmed_reference_range = None
        self.confirmed_evidence_text = None


# ── 1. 四个数各自的判据 ────────────────────────────────────────────────────


def test_the_four_counts_each_answer_their_own_question():
    rows = [
        # 判定为 H（值超范围）—— 异常、需要处理、已判定
        _Row(value="6.5"),
        # 判定为 N —— 都不是
        _Row(value="5.2"),
        # 模型标 H，值判不出来（两个值）—— **不计入 abnormal**，但需要患者处理、且未判定
        _Row(value="3.39 / 3.63", unit=None, raw_flag="H", evidence="空腹血糖 3.39 / 3.63"),
        # 患者已排除 —— 四个数都不数它（parsed 除外）
        _Row(value="7.5", status="excluded"),
    ]
    counts = metric_counts(rows)

    # row1 判成 H；row3 判不出来（两个值）但仍要患者核对；row4 已排除、四个数都不数它。
    assert counts == MetricCounts(parsed=4, abnormal=1, needs_review=2, undecided=1)


def test_the_two_predicates_are_not_the_same_read_of_one_number():
    """负控：有且只有「两个值 + 原始 H」这一行会落在 needs_review 而不落 abnormal。

    改动前，报告单用 `isAbnormal(displayFlag(...))` 数出 0（于是宣告「未见异常指标」并
    印出「当前报告的指标均在参考区间内」），确认页用 `needsReview` 数出 1。这条断言把
    两个谓词的关系写下来：`needs_review >= abnormal`，且在本例上严格大于。
    """
    row = _Row(value="3.39 / 3.63", unit=None, raw_flag="H", evidence="空腹血糖 3.39 / 3.63")

    from app.service.evidence_bridge import infer_abnormal_flag_for_metric

    assert infer_abnormal_flag_for_metric(row) is None, "这一行判不出来"
    assert needs_review(row) is True, "但模型宣称它异常 → 要患者核对"
    assert undecided(row) is True, "而它确实还没有判定"
    assert metric_counts([row]).abnormal == 0, "它**不**是异常 —— 这正是改动前两处打架的那一行"


def test_a_row_that_is_merely_undecided_is_not_put_on_the_patient():
    """判不出来、模型也没标异常的行：落 `undecided`，但**不**要患者处理。

    两者分开是本模块刻意的：并成一个数会让结论句重新有机会说谎（那一类没有「待核对」
    可显示，患者也无从处理）。
    """
    row = _Row(value="6.5", reference=None, raw_flag=None)
    assert undecided(row) is True
    assert needs_review(row) is False


@pytest.mark.parametrize(
    ("raw_flag", "expected"),
    [("H", True), ("h", True), ("HIGH", True), ("高", True), ("*", True), ("L", True), ("N", False), (None, False)],
)
def test_the_raw_marker_judgement_matches_the_front_end(raw_flag, expected):
    """抽取模型的原始标记，服务端与前端必须同判（前端对全角/小写一视同仁）。"""
    from app.service.report_metric_counts import raw_flag_claims_abnormal

    assert raw_flag_claims_abnormal(raw_flag) is expected


# ── 2. 一处实现 ────────────────────────────────────────────────────────────


def test_no_other_module_computes_these_counts():
    """**全仓库只有一处**产生这四个数。

    判据是「谁在数指标行」：任何对 `infer_abnormal_flag_for_metric` 或原始标记做
    `sum(...)` / `len([...])` 的地方都是候选。允许的只有计数模块本身。
    """
    offenders: list[str] = []
    for path in sorted((REPO_ROOT / "app").rglob("*.py")):
        if path.name == "report_metric_counts.py":
            continue
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, (ast.GeneratorExp, ast.ListComp)) and _counts_metrics(node):
                offenders.append(f"{path.relative_to(REPO_ROOT)}:{node.lineno}")
    assert not offenders, f"这些位置在自己数指标：{offenders}（应改为从 report_metric_counts 取）"


def _counts_metrics(node: ast.AST) -> bool:
    """这个推导式数的是指标行吗（调用异常判定，或读原始标记）。"""
    return any(
        isinstance(inner, ast.Call) and getattr(inner.func, "id", None) == "infer_abnormal_flag_for_metric"
        for inner in ast.walk(node)
    )


# ── 3. 结论句 ──────────────────────────────────────────────────────────────

_MODULE = REPO_ROOT / "frontend" / "src" / "pages" / "ReportDetail.jsx"
NODE = shutil.which("node")

_DRIVER = """
import fs from 'node:fs';
import path from 'node:path';
import process from 'node:process';
import { pathToFileURL } from 'node:url';
import { build } from 'esbuild';

const outfile = path.join(process.env.HEALTHFLOW_TEST_OUTDIR, 'report-detail.mjs');
await build({
  entryPoints: [process.env.HEALTHFLOW_TEST_MODULE],
  outfile, bundle: true, packages: 'external', format: 'esm', jsx: 'automatic', logLevel: 'silent',
});
const mod = await import(pathToFileURL(outfile).href);
fs.writeFileSync(
  path.join(process.env.HEALTHFLOW_TEST_OUTDIR, 'result.json'),
  JSON.stringify(JSON.parse(process.env.HEALTHFLOW_TEST_CASES).map((c) => mod.abnormalSummaryText(c))),
);
"""


@pytest.mark.skipif(NODE is None, reason="需要 node 才能驱动前端模块")
@pytest.mark.skipif(
    not (REPO_ROOT / "frontend" / "node_modules" / "esbuild").is_dir(),
    reason="需要 frontend/node_modules（esbuild）才能转译 JSX",
)
def test_the_conclusion_sentence_is_refused_while_a_row_is_undecided():
    """有未判定的行时不许说「均在参考区间内」—— 那是假话，而患者会据此放心。

    驱动的是**真实的**模块（先例 `tests/test_frontend_page_count.py`）。
    """
    cases = [
        # 0 异常、0 未判定 → 可以宣告
        {"counts": {"parsed": 3, "abnormal": 0, "needs_review": 0, "undecided": 0}},
        # 0 异常、有 1 项未判定 → **不许**宣告
        {"counts": {"parsed": 3, "abnormal": 0, "needs_review": 1, "undecided": 1}},
        # 有异常 → 那句话本来就不出现（走异常分支）
        {"counts": {"parsed": 3, "abnormal": 2, "needs_review": 2, "undecided": 0}},
    ]
    frontend = REPO_ROOT / "frontend"
    work = frontend / "node_modules" / f".tmp-counts-{os.getpid()}"
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
                "HEALTHFLOW_TEST_MODULE": str(_MODULE),
                "HEALTHFLOW_TEST_OUTDIR": str(work),
                "HEALTHFLOW_TEST_CASES": json.dumps(cases),
            },
            cwd=frontend,
            timeout=180,
        )
        assert proc.returncode == 0, f"驱动失败:\n{proc.stdout}\n{proc.stderr}"
        got = json.loads((work / "result.json").read_text(encoding="utf-8"))
    finally:
        shutil.rmtree(work, ignore_errors=True)

    assert got[0]["title"] == "未见异常指标"
    assert "均在参考区间内" in got[0]["description"], "没有任何未判定行时，那句话是对的"

    assert got[1]["title"] == "未见异常指标"
    assert "均在参考区间内" not in got[1]["description"], "有未判定行时**不许**说那句话"
    assert "未能判定" in got[1]["description"], "改说清是哪一种情况"

    assert got[2]["title"] == "异常指标 2 项"
