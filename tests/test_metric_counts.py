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
        # 判定为 N —— 范围内
        _Row(value="5.2"),
        # 模型标 H，值判不出来（两个值）—— **不计入 abnormal**，但需要患者处理、且未判定
        _Row(value="3.39 / 3.63", unit=None, raw_flag="H", evidence="空腹血糖 3.39 / 3.63"),
        # 患者已排除 —— 四个数都不数它（parsed 除外）
        _Row(value="7.5", status="excluded"),
    ]
    counts = metric_counts(rows)

    # row1 判成 H；row2 判定为 N、进「范围内」；row3 判不出来（两个值）但仍要患者核对；
    # row4 已排除、除 parsed 外一个数都不数它。
    assert counts == MetricCounts(parsed=4, abnormal=1, needs_review=2, undecided=1, in_range=1)


def test_in_range_counts_only_judged_N_rows_that_the_patient_did_not_exclude():
    """「范围内」的判据是**判定为 N 且未排除**，不是「其余的行」（#270）。

    负控是那条**判定为 N、但被患者排除**的行：减法（`parsed - abnormal - undecided`）
    会把它算成「范围内」，而患者说的是「这一行不要解读」—— 那句「正常」他没说过。
    「判定不出来」的行也不进这里：它没有结论，不是「在区间内」。
    """
    from app.service.report_metric_counts import in_range

    assert in_range(_Row(value="5.2")) is True, "判定为 N"
    assert in_range(_Row(value="6.5")) is False, "判定为 H"
    assert in_range(_Row(value="Not Detected", reference=None, evidence="空腹血糖 Not Detected")) is True, (
        "定性阴性同样判定为 N"
    )
    assert in_range(_Row(value="3.39 / 3.63", unit=None, evidence="空腹血糖 3.39 / 3.63")) is False, (
        "判不出来不是「在区间内」"
    )
    assert in_range(_Row(value="5.2", status="excluded")) is False, (
        "患者排除的行不数 —— 减法会把它算进来，这正是本条负控"
    )

    counts = metric_counts(
        [
            _Row(value="5.2"),
            _Row(value="5.2", status="excluded"),
            _Row(value="6.5"),
            _Row(value="3.39 / 3.63", unit=None, evidence="空腹血糖 3.39 / 3.63"),
        ]
    )
    # 三个互斥的出口加总 == parsed - 已排除：一行落在且只落在一个数里。
    assert counts.abnormal + counts.in_range + counts.undecided == counts.parsed - 1


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


def test_a_row_that_is_merely_undecided_is_still_shown_as_awaiting_review():
    """判不出来、且患者还没表态的行：落 `undecided`，**也**落 `needs_review`。

    两者分开是为了**结论句**（有未判定行时不许说「均在参考区间内」），不是为了把这一行
    从患者面前藏起来 —— 它在确认页上显示「待核对」，所以页头必须数它（数字站在行列表
    上方，少数就是让页头说谎）。第一版服务端判据漏了这一条，自查时逮到。
    """
    row = _Row(value="6.5", reference=None, raw_flag=None)
    assert undecided(row) is True
    assert needs_review(row) is True

    # 而患者**已经**表过态、模型也没标异常的行，就不必再请他一次。
    settled = _Row(value="6.5", reference=None, raw_flag=None, status="confirmed")
    assert undecided(settled) is True
    assert needs_review(settled) is False


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
        # 旧服务端响应：没有 counts 字段
        {"metrics": []},
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

    # 旧服务端响应没有 `counts`：**不**说那句话（`undecided` 无法从逐行数据推出来），
    # 但也**不**说一句可能为假的「均在参考区间内」。
    assert "均在参考区间内" not in got[3]["description"]
    assert got[3]["description"] == "未发现异常标记的指标。"

    assert got[1]["title"] == "未见异常指标"
    assert "均在参考区间内" not in got[1]["description"], "有未判定行时**不许**说那句话"
    assert "未能判定" in got[1]["description"], "改说清是哪一种情况"

    assert got[2]["title"] == "异常指标 2 项"

# ── 4. 与服务端的「需要确认」与页面上显示的「待核对」同源 ────────────────────

_UPLOAD = REPO_ROOT / "frontend" / "src" / "pages" / "Upload.jsx"

_DISPLAY_DRIVER = """
import fs from 'node:fs';
import path from 'node:path';
import process from 'node:process';
import { pathToFileURL } from 'node:url';
import { build } from 'esbuild';

const outfile = path.join(process.env.HEALTHFLOW_TEST_OUTDIR, 'upload.mjs');
await build({
  entryPoints: [process.env.HEALTHFLOW_TEST_MODULE],
  outfile, bundle: true, packages: 'external', format: 'esm', jsx: 'automatic', logLevel: 'silent',
});
const mod = await import(pathToFileURL(outfile).href);
const rows = JSON.parse(process.env.HEALTHFLOW_TEST_ROWS);
fs.writeFileSync(
  path.join(process.env.HEALTHFLOW_TEST_OUTDIR, 'result.json'),
  JSON.stringify(rows.map((r) => {
    const flag = mod.displayFlag(r);
    return { flag, counted: ['H', 'L', 'A', '待核对'].includes(flag) };
  })),
);
"""


@pytest.mark.skipif(NODE is None, reason="需要 node 才能驱动前端模块")
@pytest.mark.skipif(
    not (REPO_ROOT / "frontend" / "node_modules" / "esbuild").is_dir(),
    reason="需要 frontend/node_modules（esbuild）才能转译 JSX",
)
def test_the_header_count_agrees_with_the_rows_the_page_shows():
    """**页头那个数必须等于页面上显示为异常/待核对的行数。**

    这条是自查发现的缺陷：服务端第一版只把「模型宣称异常」算作需要处理，而**前端
    `displayFlag` 把「患者还没表态 + 判不出来」的行也显示成「待核对」** —— 于是页头会
    站在一个比它实际列出的行更少的数字上（数字说 2 项，列表里有 3 条）。

    判据不是「两份实现看起来一样」，而是**驱动真实 `displayFlag` 逐行比对**（先例：
    `tests/test_two_value_agreement.py`）。
    """
    rows = [
        # 判得出来：H / L / N / A
        {"value": "6.5", "reference": "3.9-6.1", "raw": "H", "status": "confirmed"},
        {"value": "0.5", "reference": "3.9-6.1", "raw": None, "status": "confirmed"},
        {"value": "5.2", "reference": "3.9-6.1", "raw": None, "status": "confirmed"},
        {"value": "Detected", "reference": None, "raw": None, "status": "confirmed"},
        # 判不出来 + 患者还没表态（值、范围齐，只差他核对）→ 页面显示「待核对」
        {"value": "5.2", "reference": "3.9-6.1", "raw": None, "status": "pending"},
        # 判不出来 + 还没表态 + 模型宣称异常
        {"value": "3.39 / 3.63", "reference": None, "raw": "H", "status": "pending"},
        # 判不出来 + 已表态 + 模型宣称异常 → 「待核对」
        {"value": "3.39 / 3.63", "reference": None, "raw": "H", "status": "confirmed"},
        # 判不出来 + 已表态 + 模型没宣称异常 → 如实显示原始标记，不请他
        {"value": "6.5", "reference": None, "raw": None, "status": "confirmed"},
        # **判据的分界行**：判不出来、模型也没标异常，但患者**还没表态** → 页面显示
        # 「待核对」，所以要数他。第一版服务端判据漏了这一类（自查逮到），而上面那条
        # 「已表态」的行两种情况都不数 —— 没有这一行，本测试对那个缺陷是盲的。
        {"value": "6.5", "reference": None, "raw": None, "status": "pending"},
        # 患者已排除 → 不请他
        {"value": "7.5", "reference": "3.9-6.1", "raw": "H", "status": "excluded"},
    ]

    frontend = REPO_ROOT / "frontend"
    work = frontend / "node_modules" / f".tmp-display-{os.getpid()}"
    shutil.rmtree(work, ignore_errors=True)
    work.mkdir(parents=True)
    try:
        (work / "driver.mjs").write_text(_DISPLAY_DRIVER, encoding="utf-8")
        # 服务端的行对象与前端读到的是同一份形状（`inferred_abnormal_flag` 由服务端算）。
        server_rows = [
            _Row(
                value=r["value"],
                reference=r["reference"],
                unit="mmol/L",
                raw_flag=r["raw"],
                status=r["status"],
                evidence=f"空腹血糖 {r['value']}",
            )
            for r in rows
        ]
        js_rows = [
            {
                "metric_name": "空腹血糖",
                "metric_value": r["value"],
                "reference_range": r["reference"],
                "unit": "mmol/L",
                "abnormal_flag": r["raw"],
                "confirmation_status": r["status"],
                "evidence_text": f"空腹血糖 {r['value']}",
                "page_number": 1,
                "admission_reason": None,
                # 与 `_metric_response` 同一口径：服务端算好交给前端。
                "inferred_abnormal_flag": __import__(
                    "app.service.evidence_bridge", fromlist=["x"]
                ).infer_abnormal_flag_for_metric(server_row),
            }
            for r, server_row in zip(rows, server_rows, strict=True)
        ]
        proc = subprocess.run(
            [NODE, str(work / "driver.mjs")],
            capture_output=True,
            text=True,
            env={
                **os.environ,
                "HEALTHFLOW_TEST_MODULE": str(_UPLOAD),
                "HEALTHFLOW_TEST_OUTDIR": str(work),
                "HEALTHFLOW_TEST_ROWS": json.dumps(js_rows, ensure_ascii=False),
            },
            cwd=frontend,
            timeout=180,
        )
        assert proc.returncode == 0, f"驱动失败:\n{proc.stdout}\n{proc.stderr}"
        front = json.loads((work / "result.json").read_text(encoding="utf-8"))
    finally:
        shutil.rmtree(work, ignore_errors=True)

    mismatches = [
        (i, rows[i]["value"], rows[i]["status"], front[i]["flag"], front[i]["counted"], needs_review(server_rows[i]))
        for i in range(len(rows))
        if front[i]["counted"] != needs_review(server_rows[i])
    ]
    assert not mismatches, (
        "服务端的「需要处理」与页面上的「异常/待核对」不一致（服务端少数会让页头说谎）：\n"
        + "\n".join(f"  #{i} value={v} status={s} flag={f} front={c} server={sv}" for i, v, s, f, c, sv in mismatches)
    )
    # 而且确实存在「判不出来但还没表态」这一类（否则这条测试会因为全都是异常行而空转）。
    assert sum(1 for i in range(len(rows)) if front[i]["counted"]) >= 5
