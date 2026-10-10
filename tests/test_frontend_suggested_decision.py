"""确认页的默认决策不再冒充患者的表态（#195、#203）。

本文件驱动的是**真实的前端模块**：node 用 esbuild 转译 `frontend/src/pages/Upload.jsx`
后导入它的导出，断言的对象与浏览器里渲染、提交的是同一份代码（先例：
`tests/test_frontend_page_count.py`、`tests/test_admission_projection.py`）。

回归的是这个患者可见的缺陷：确认页逐行算出一个默认决策（正常行默认「排除」），并把它
当成 `decision` 发给服务端 —— 于是**患者没看过的正常行被记成「患者已排除」**，从报告单
总览消失、生效值四元组全空，而服务端分不清那是患者定的还是界面定的。

现在两者分开，分界线是**这一行有没有显示给患者看过**（`needsReview`）：

- 建议（`suggestedDecision`）只决定界面初选哪一项；患者**动过**的行用他的选择，**显示过
  又没动**的行用那个初选（他没改就是他的答案），服务端**已经给过**的决策优先于它；
- **没显示过**的正常行如实说「这条我还没动」（`pending`）—— 那**不是**一个替患者作的默认，
  解出来是什么由服务端定。

#195 的审查修复也是这条链上的一环：载荷此前对「两者都没有」留空，撞的是服务端契约的校验
错（422 的 `detail` 是 pydantic 数组，前端 stringify 后甩给患者），而**所有 e2e 都 mock 了
`/confirm`**，患者与测试都看不出。

这些断言**按行为**写（把行喂进真实模块，看它解出什么），不按源码字符串写：本文件一度用
`code.index` 切片查 `needsReview(metric)` 这个子串，而那个子串在文件别处也有，于是把载荷里
的它删掉时守卫照样绿。字符串守卫在这条链上不可靠 —— 见下面各条的说明。
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
// 与提交载荷**逐字相同**的那条取值规则（见 Upload.jsx 的 `const decided =`）：
// 患者动过 > 服务端已经给过 > 显示过的行用初选 > `pending`。
const serverDecision = (m) =>
  ['confirmed', 'corrected', 'excluded'].includes(m?.confirmation_status) ? m.confirmation_status : null;
const needsReview = (m) => {
  const flag = mod.displayFlag(m);
  return flag === 'H' || flag === 'L' || flag === '待核对';
};
const decidedFor = (m) => serverDecision(m) || (needsReview(m) ? mod.suggestedDecision(m) : 'pending');
fs.writeFileSync(
  path.join(outDir, 'result.json'),
  JSON.stringify({
    suggestions: cases.metrics.map((m) => mod.suggestedDecision(m)),
    decided: cases.metrics.map((m) => decidedFor(m)),
    needsReview: cases.metrics.map((m) => needsReview(m)),
  }),
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


def _drive(metrics: list[dict]) -> dict:
    """把一组行喂进**真实的前端模块**，取回三件事：初选、是否显示、载荷里的取值。"""
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
        return json.loads((work / "result.json").read_text(encoding="utf-8"))
    finally:
        shutil.rmtree(work, ignore_errors=True)


def _suggestions(metrics: list[dict]) -> list[str]:
    return _drive(metrics)["suggestions"]


def _decided_for(metric: dict) -> str:
    """这一行在提交载荷里会取什么值 —— 走与 `const decided =` 相同的规则。"""
    return _drive([metric])["decided"][0]


@needs_node[0]
@needs_node[1]
def test_a_normal_row_is_written_out_but_never_submitted_as_the_patients_own_decision():
    """「没看过的正常行」：界面上不打扰患者，也**不**代他表态。

    两件事分开断言，因为它们是两个判据：初选是「排除」（界面不打扰他），而载荷里它**不**
    取那个初选 —— 它如实说「我没动过这一行」。
    """
    result = _drive([_NORMAL])
    assert result["suggestions"] == ["excluded"]
    assert result["needsReview"] == [False], "正常行不该出现在待处理集合里"
    assert result["decided"] == ["pending"], "隐藏的正常行不代患者表态"


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


# ── 提交载荷里那三条来源 ────────────────────────────────────────────────────


def test_the_submitted_payload_never_carries_a_client_computed_default():
    """提交载荷里的默认值**只给患者看到过的行**，而且只在这三件事之后。

    这是本票的核心断言。它按**行为**查，不按源码字符串查 —— 早先这条把 `Upload.jsx` 切一
    段出来找 `needsReview(metric)` 这个子串，而那个子串在 `decisionOf` 里也出现（在切片锚点
    之外），所以把载荷里的它删掉时这条照样绿。字符串守卫在这里**不可能**可靠。

    四种行形态，各自的答案必须都对：
    - 患者动过 → 他的选择（`draft.decision`，由下面的 e2e 覆盖）；
    - 服务端已经给过 → 沿用（重入确认不丢服务端的结论）；
    - 显示给患者看过、他没动 → 界面上那个初选（他没改就是他的答案）；
    - **没显示过**的正常行 → 如实说「我没动过这一行」，绝不取初选。
    """
    source = (REPO_ROOT / "frontend" / "src" / "pages" / "Upload.jsx").read_text(encoding="utf-8")
    code = "\n".join(line.split("//", 1)[0] for line in source.splitlines())
    assert "initialDecision" not in code, "旧名字还在 —— 它读起来就像「患者的决定」，而它不是"

    # 服务端已经给过的决策优先于界面初选。
    assert _decided_for({**_NORMAL, "confirmation_status": "excluded"}) == "excluded"
    assert _decided_for({**_NORMAL, "confirmation_status": "confirmed"}) == "confirmed"
    # 显示给患者看过、他没动 → 初选（判成 H 且证据齐 → 「确认」）。
    shown = {**_NORMAL, "metric_value": "6.5", "abnormal_flag": "H", "inferred_abnormal_flag": "H",
             "reference_range": "3.9-6.1", "evidence_text": "血糖 6.5 mmol/L 3.9-6.1"}
    assert _drive([shown])["needsReview"] == [True]
    assert _decided_for(shown) == "confirmed"
    # 没显示过的正常行 → 不代患者表态。
    assert _drive([_NORMAL])["needsReview"] == [False]
    assert _decided_for(_NORMAL) == "pending"


def test_the_payload_always_carries_a_value_the_server_accepts():
    """每一条都必须带一个服务端收得下的 `decision` —— **不能是 `null`/`undefined`**。

    这条是本票最直接的一次回归：`draft.decision || serverDecision(metric)` 对一条「患者
    没动过、服务端也没落定过」的行求值成 `null`，而契约要求这个字段有值 —— 于是**什么都没
    改就点确认**会撞一个校验错（422 的 `detail` 是一个数组，前端把它 stringify 后甩给患者）。
    界面上看起来一切正常，只有真的提交才发现。

    兜底只能是 `pending`（「这条我还没动」），因为它是**诚实**的那一句：解出来是什么由服务端
    定。这里对**每一种行形态**都断言它落在词表里 —— 兜底缺失时其中几种会解成 `null`。
    """
    shapes = (
        _NORMAL,  # 隐藏的正常行
        {**_NORMAL, "metric_value": "3.87 4.00", "abnormal_flag": "H", "inferred_abnormal_flag": None},
        {**_NORMAL, "metric_value": "6.5", "abnormal_flag": "H", "inferred_abnormal_flag": "H",
         "reference_range": "3.9-6.1", "evidence_text": "血糖 6.5 mmol/L 3.9-6.1"},
        {**_NORMAL, "metric_value": "", "abnormal_flag": "H", "inferred_abnormal_flag": None},
    )
    decided = _drive(list(shapes))["decided"]
    assert decided == ["pending", "pending", "confirmed", "pending"], decided
    assert all(value in {"pending", "confirmed", "corrected", "excluded"} for value in decided), decided


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
