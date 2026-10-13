"""患者侧的解读失败文案（GLOSSARY.md 的「解读失败」，PRD #226）。

驱动的是**真实的前端模块** `frontend/src/interpretationFailure.js`，断言的对象与浏览器
里渲染用的是同一份代码（先例 `tests/test_frontend_page_count.py`）。

要钉住的是两件患者可见的事：

1. **五个成因渲染成五句不同的话。** 此前四层各写一句兜底文案，措辞已经分叉
   （「请重试」/「请重新上传或稍后重试。」/「解析失败」），患者只看得到其中一句。
2. **只有两个成因会说「重新上传」。** 今天它永远是唯一被给出的动作 —— 而同一份材料
   再传一遍既救不了「我们这边读不出来」，也救不了「服务当时不可用」，还会留下第二份
   记录（ADR-0006 记下的那句「请重新上传对这类失败是误导的」）。

第三条钉的是**不许承诺**：「稍后自动重试」在重试次数已经用完、报告终态是 `failed` 时
是假的。承诺一件可能不会发生的事比不说更糟，所以三句「我们这边」的话都不许出现它。
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
MODULE = REPO_ROOT / "frontend" / "src" / "interpretationFailure.js"

NODE = shutil.which("node")

_DRIVER = """
import fs from 'node:fs';
import path from 'node:path';
import process from 'node:process';
import { pathToFileURL } from 'node:url';
import { build } from 'esbuild';

const modulePath = process.env.HEALTHFLOW_TEST_MODULE;
const outDir = process.env.HEALTHFLOW_TEST_OUTDIR;
const outfile = path.join(outDir, 'interpretation-failure.mjs');
await build({
  entryPoints: [modulePath],
  outfile,
  bundle: true,
  packages: 'external',
  format: 'esm',
  logLevel: 'silent',
});
const mod = await import(pathToFileURL(outfile).href);
fs.writeFileSync(
  path.join(outDir, 'result.json'),
  JSON.stringify(
    JSON.parse(process.env.HEALTHFLOW_TEST_BLOCKS).map((block) => ({
      line: mod.interpretationFailureLine(block),
      text: mod.interpretationFailureText(block),
      sentences: mod.FAILURE_SENTENCES,
      actions: mod.FAILURE_ACTIONS,
    })),
  ),
);
"""

CAUSES = [
    "material_unreadable",
    "service_unavailable",
    "provider_rejected",
    "provider_unavailable",
    "content_unrecognized",
]
#: 只有这两个成因会请患者动手 —— 与后端 `PATIENT_ACTION_BY_FAILURE` 同一份口径。
UPLOAD_AGAIN = {"material_unreadable", "content_unrecognized"}
BLOCKS = [{"cause": cause, "retryable": False, "action": "x", "failed_files": []} for cause in CAUSES]


def _drive(blocks):
    frontend = REPO_ROOT / "frontend"
    work = frontend / "node_modules" / f".tmp-interpretation-failure-{os.getpid()}"
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
                "HEALTHFLOW_TEST_BLOCKS": json.dumps(blocks),
            },
            cwd=frontend,
            timeout=180,
        )
        assert proc.returncode == 0, f"驱动失败:\n{proc.stdout}\n{proc.stderr}"
        return json.loads((work / "result.json").read_text(encoding="utf-8"))
    finally:
        shutil.rmtree(work, ignore_errors=True)


@pytest.mark.skipif(NODE is None, reason="需要 node 才能驱动前端模块")
@pytest.mark.skipif(
    not (REPO_ROOT / "frontend" / "node_modules" / "esbuild").is_dir(),
    reason="需要 frontend/node_modules（esbuild）才能转译；先在前端目录 npm install",
)
def test_every_cause_renders_a_distinct_sentence():
    results = _drive(BLOCKS)
    lines = [item["line"] for item in results]

    assert len(set(lines)) == len(CAUSES), f"五个成因必须说五句不同的话，实际：{lines}"
    for cause, line in zip(CAUSES, lines, strict=True):
        assert line, f"{cause} 渲染成了空话"


@pytest.mark.skipif(NODE is None, reason="需要 node 才能驱动前端模块")
@pytest.mark.skipif(
    not (REPO_ROOT / "frontend" / "node_modules" / "esbuild").is_dir(),
    reason="需要 frontend/node_modules（esbuild）才能转译；先在前端目录 npm install",
)
def test_only_two_causes_ever_ask_the_patient_to_upload_again():
    results = _drive(BLOCKS)
    asking = {
        cause
        for cause, item in zip(CAUSES, results, strict=True)
        if "重新上传" in item["line"]
    }
    assert asking == UPLOAD_AGAIN, f"只有这两种成因该请患者重传，实际：{asking}"


@pytest.mark.skipif(NODE is None, reason="需要 node 才能驱动前端模块")
@pytest.mark.skipif(
    not (REPO_ROOT / "frontend" / "node_modules" / "esbuild").is_dir(),
    reason="需要 frontend/node_modules（esbuild）才能转译；先在前端目录 npm install",
)
def test_no_cause_promises_an_automatic_retry():
    """终态的失败不许承诺「马上会自动重试」—— 重试次数可能已经用完。

    「正在重试」的实时提示由 `extraction_job.attempt_count` 给出，那是**另一个**时刻。
    """
    results = _drive(BLOCKS)
    for cause, item in zip(CAUSES, results, strict=True):
        for banned in ("自动重试", "会自动", "马上重试", "正在重试"):
            assert banned not in item["line"], f"{cause} 的话里承诺了 {banned}：{item['line']}"


@pytest.mark.skipif(NODE is None, reason="需要 node 才能驱动前端模块")
@pytest.mark.skipif(
    not (REPO_ROOT / "frontend" / "node_modules" / "esbuild").is_dir(),
    reason="需要 frontend/node_modules（esbuild）才能转译；先在前端目录 npm install",
)
def test_the_vocabulary_is_complete_and_the_unknown_case_says_so():
    """后端词表里的每一个成因前端都要有话说 —— 漏一个是患者看到空白。

    没有成因时（历史行）返回 `None`，调用点退回状态级的那句话，而不是编一个成因。
    """
    results = _drive([*BLOCKS, {"failed_files": []}, None])
    sentences = results[0]["sentences"]
    for cause in CAUSES:
        assert cause in sentences, f"{cause} 在前端没有对应的话"

    for item in results[len(CAUSES):]:
        assert item["line"] is None, "没有成因时不许编一句话出来"


@pytest.mark.skipif(NODE is None, reason="需要 node 才能驱动前端模块")
@pytest.mark.skipif(
    not (REPO_ROOT / "frontend" / "node_modules" / "esbuild").is_dir(),
    reason="需要 frontend/node_modules（esbuild）才能转译；先在前端目录 npm install",
)
def test_our_own_failures_are_not_red():
    """「我们这边出的问题」不许用红色。

    红色说的是「你上传的东西有问题」，而这一整票要消灭的正是那个印象 —— 一份材料没
    问题、是我们这边读不出来时，患者不该看到一个指责他输入的信号。三个「我们这边」的
    成因因此是 `warning`；只有指向患者材料的两个才是 `error`。
    """
    blocks = [
        {"cause": cause, "failed_files": []} for cause in CAUSES
    ]
    results = _drive(blocks)
    colors = {cause: item["text"]["color"] for cause, item in zip(CAUSES, results, strict=True)}

    assert colors["material_unreadable"] == "error"
    assert colors["content_unrecognized"] == "error"
    for cause in ("service_unavailable", "provider_rejected", "provider_unavailable"):
        assert colors[cause] == "warning", f"{cause} 是我们这边的问题，不该是红色"


@pytest.mark.skipif(NODE is None, reason="需要 node 才能驱动前端模块")
@pytest.mark.skipif(
    not (REPO_ROOT / "frontend" / "node_modules" / "esbuild").is_dir(),
    reason="需要 frontend/node_modules（esbuild）才能转译；先在前端目录 npm install",
)
def test_the_no_cause_line_lives_in_the_module_not_in_the_callers():
    """没有成因时的那句话住在模块里，调用点各写一句兜底正是本票要消灭的形状。

    静态断言：页面不得自己再写一句「报告智能解读失败…」这类兜底。
    """
    for page in ("pages/Upload.jsx", "pages/ReportDetail.jsx"):
        source = (REPO_ROOT / "frontend" / "src" / page).read_text(encoding="utf-8")
        assert "报告智能解读失败" not in source, f"{page} 自己写了一句兜底文案"
        assert "请稍后重试。" not in source or page == "pages/ReportDetail.jsx", page
