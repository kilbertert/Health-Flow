"""确认页的默认决策不再冒充患者的表态（#195）。

本文件驱动的是**真实的前端模块**：node 用 esbuild 转译 `frontend/src/pages/Upload.jsx`
后导入它的导出，断言的对象与浏览器里渲染、提交的是同一份代码（先例：
`tests/test_frontend_page_count.py`、`tests/test_admission_projection.py`）。

回归的是这个患者可见的缺陷：确认页逐行算出一个默认决策（正常行默认「排除」），并把它
当成 `decision` 发给服务端 —— 于是**患者没看过的正常行被记成「患者已排除」**，从报告单
总览消失、生效值四元组全空，而服务端分不清那是患者定的还是界面定的。

现在两者分开：建议（`suggestedDecision`）只决定界面初选哪一项，表态只由患者给出。提交
载荷里的每一条只能是「患者动过它」或「服务端已经给过决策」，两者都没有时留空 —— 服务端
按它自己的规则回一个明确的拒绝，而不是收到一个冒充患者决定的默认值。
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
MODULE = REPO_ROOT / "frontend" / "src" / "pages" / "Upload.jsx"
NODE = shutil.which("node")

_DRIVER = """
import fs from 'node:fs';
import path from 'node:path';
import process from 'node:process';
import { pathToFileURL } from 'node:url';
import { build } from 'esbuild';

const modulePath = process.env.HEALTHFLOW_TEST_MODULE;
const outDir = process.env.HEALTHFLOW_TEST_OUTDIR;
const outfile = path.join(outDir, 'upload.mjs');
await build({
  entryPoints: [modulePath],
  outfile,
  bundle: true,
  packages: 'external',
  format: 'esm',
  jsx: 'automatic',
  logLevel: 'silent',
});
const mod = await import(pathToFileURL(outfile).href);
const cases = JSON.parse(process.env.HEALTHFLOW_TEST_CASES);
fs.writeFileSync(
  path.join(outDir, 'result.json'),
  JSON.stringify({ suggestions: cases.metrics.map((m) => mod.suggestedDecision(m)) }),
);
"""

needs_node = [
    pytest.mark.skipif(NODE is None, reason="需要 node 才能驱动前端模块"),
    pytest.mark.skipif(
        not (REPO_ROOT / "frontend" / "node_modules" / "esbuild").is_dir(),
        reason="需要 frontend/node_modules（esbuild）；先在前端目录 npm install",
    ),
]

# 一条「患者没处理过」的正常指标：值在范围内、模型没标异常。
_NORMAL = {
    "id": 1,
    "metric_name": "血红蛋白",
    "metric_value": "138",
    "abnormal_flag": "N",
    "inferred_abnormal_flag": "N",
    "confirmation_status": "pending",
    "page_number": 1,
    "evidence_text": "血红蛋白 138 g/L 130-175",
}


def _suggestions(metrics: list[dict]) -> list[str]:
    frontend = REPO_ROOT / "frontend"
    work = frontend / "node_modules" / f".tmp-suggested-{os.getpid()}"
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
                "HEALTHFLOW_TEST_MODULE": str(MODULE),
                "HEALTHFLOW_TEST_OUTDIR": str(work),
                "HEALTHFLOW_TEST_CASES": json.dumps({"metrics": metrics}),
            },
            cwd=frontend,
            timeout=180,
        )
        assert proc.returncode == 0, f"驱动失败:\n{proc.stdout}\n{proc.stderr}"
        return json.loads((work / "result.json").read_text(encoding="utf-8"))["suggestions"]
    finally:
        shutil.rmtree(work, ignore_errors=True)


@needs_node[0]
@needs_node[1]
def test_a_normal_row_is_written_out_but_never_submitted_as_the_patients_own_decision():
    """「没看过的正常行」：界面上不打扰患者，但**不**代他表态。

    这里断言的是建议值本身（界面初选「排除」是对的 —— 患者不需要看这一行）。真正的
    核心断言在下面那条静态守卫里：那个建议**不进提交载荷**。
    """
    assert _suggestions([_NORMAL]) == ["excluded"]


@needs_node[0]
@needs_node[1]
def test_a_row_the_server_says_cannot_be_read_suggests_review_not_confirmation():
    """服务端说这一行没进解读 → 建议「待核对」，绝不建议「确认」。

    #129 的守卫：多值/带符号的值会被后端整行丢掉，界面若默认「确认」，患者会以为它参与了
    解读（报告 44 的三个异常项就是这么消失的）。今天判据是 #129 传承下来的那一条 ——
    评估之前没有准入结论，只能看「值解析不出一个数」。
    """
    unreadable = {**_NORMAL, "metric_value": "3.87 4.00", "abnormal_flag": "H", "inferred_abnormal_flag": None}
    assert _suggestions([unreadable]) == ["pending"]


@needs_node[0]
@needs_node[1]
def test_the_suggestion_is_not_the_patients_decision_for_no_row_shape():
    """四种形态下，建议都只是建议：没有任何一条会被建议成需要患者事后补救的东西。

    这条按形态列全，是为了让「建议」与「表态」的边界在**每一种输入**上都被看过一遍：
    正常行建议排除、异常行建议确认、判不出的建议待核对 —— 而它们全都**不**进载荷。
    """
    abnormal = {**_NORMAL, "id": 2, "metric_value": "6.5", "abnormal_flag": "H", "inferred_abnormal_flag": "H",
                "reference_range": "3.9-6.1", "evidence_text": "血糖 6.5 mmol/L 3.9-6.1"}
    abnormal_no_evidence = {**abnormal, "id": 3, "evidence_text": None}
    # 患者**已经排除**的一行、且模型标了异常：界面初选「待核对」而不是「已排除」——
    # 那是对的，因为它是「建议」而非表态：患者把这一行重新看到、再决定一次。真正「患者
    # 排除过」这件事由 `serverDecision` 单独承载（见下面的守卫），两者不混。
    excluded = {**_NORMAL, "id": 4, "confirmation_status": "excluded", "abnormal_flag": "H",
                "inferred_abnormal_flag": None}
    assert _suggestions([_NORMAL, abnormal, abnormal_no_evidence, excluded]) == [
        "excluded",  # 正常：不打扰
        "confirmed",  # 异常且证据齐：建议确认（患者可改）
        "pending",  # 异常但缺证据：建议核对
        "pending",  # 模型标异常但判定为空：建议核对
    ]


# ── 静态守卫：建议不得进入提交载荷 ──────────────────────────────────────────


def test_the_submitted_payload_never_carries_a_client_computed_default():
    """提交载荷里不得出现 `suggestedDecision`（或旧的 `initialDecision`）。

    这是本票的核心断言，而它必须按**代码**查而不是按行为查：一个「草稿没设就落回建议」的
    实现，在界面上与正确实现看起来完全一样，只在服务端落库后才发现「患者没看过的行被记成
    已排除」。所以断言落在载荷构造那一段源码上。
    """
    source = (REPO_ROOT / "frontend" / "src" / "pages" / "Upload.jsx").read_text(encoding="utf-8")
    code = "\n".join(line.split("//", 1)[0] for line in source.splitlines())
    assert "initialDecision" not in code, "旧名字还在 —— 它读起来就像「患者的决定」，而它不是"

    start = code.index("const observations = (result.metrics")
    payload = code[start : code.index("try {", start)]
    assert "suggestedDecision" not in payload, "建议不得进入提交载荷"
    assert "serverDecision" in payload and "draft.decision" in payload, (
        "载荷只该认「患者动过它」或「服务端已经给过决策」"
    )


def test_the_drafts_do_not_prefill_a_decision():
    """草稿**不预填** `decision`。

    预填会让每一行看起来都像患者选过 —— 于是「草稿里有没有这一项」不再能回答「患者动过
    它吗」，而载荷正是靠这个问题决定发什么。
    """
    source = (REPO_ROOT / "frontend" / "src" / "pages" / "Upload.jsx").read_text(encoding="utf-8")
    anchor = source.index("function initialDrafts")
    drafts = source[anchor : source.index("function ", anchor + 10)]
    code = "\n".join(line.split("//", 1)[0] for line in drafts.splitlines())
    assert "decision:" not in code, "initialDrafts 不得预填 decision"


def test_server_decision_only_accepts_a_real_decision():
    """`serverDecision` 只认三个真正的决定，`pending` 不算（请求词表也不收它）。"""
    source = (REPO_ROOT / "frontend" / "src" / "pages" / "Upload.jsx").read_text(encoding="utf-8")
    body = source[source.index("function serverDecision") : source.index("}", source.index("function serverDecision"))]
    for decision in ("confirmed", "corrected", "excluded"):
        assert f"'{decision}'" in body, decision
    assert "'pending'" not in body, "`pending` 不是服务端给过的决策"
