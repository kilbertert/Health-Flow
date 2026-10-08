"""`hasKnownPageCount`（页数未知时隐藏翻页器，#170）。

本文件驱动的是**真实的前端模块**：node 导入 `frontend/src/pages/ReportDetail.jsx`
导出的 `hasKnownPageCount`，断言的对象与浏览器里渲染用的是同一份代码（先例：
`tests/test_frontend_value_unusable.py` 用同样的方式驱动 `Upload.jsx`）。

回归的是「未知被说成共 1 页」这条：

- `page_count: null`（服务端读不出页数）与 `page_count: 1`（真的只有一页）必须得到
  **相反**的答案，否则翻页器会显示一个假的页数。
- 字段缺失（更旧的服务端响应）同样按未知处理 —— 不能因为 `undefined` 而落到某个
  默认值上。
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
MODULE = REPO_ROOT / "frontend" / "src" / "pages" / "ReportDetail.jsx"

NODE = shutil.which("node")

_DRIVER = """
import fs from 'node:fs';
import path from 'node:path';
import process from 'node:process';
import { pathToFileURL } from 'node:url';
import { build } from 'esbuild';

const modulePath = process.env.HEALTHFLOW_TEST_MODULE;
const outDir = process.env.HEALTHFLOW_TEST_OUTDIR;
const outfile = path.join(outDir, 'report-detail.mjs');
await build({
  entryPoints: [modulePath],
  outfile,
  // 本地相对导入打进同一份产物（转译后落在别处，相对路径会失效），
  // npm 依赖保持外部 —— 它们从 frontend/node_modules 正常解析。
  bundle: true,
  packages: 'external',
  format: 'esm',
  jsx: 'automatic',
  logLevel: 'silent',
});
const mod = await import(pathToFileURL(outfile).href);
fs.writeFileSync(
  path.join(outDir, 'result.json'),
  JSON.stringify(JSON.parse(process.env.HEALTHFLOW_TEST_CASES).map((m) => mod.hasKnownPageCount(m))),
);
"""

CASES = [
    # 真的只有一页：翻页器显示（simple 形态）。
    {"name": "one_page", "file": {"page_count": 1}, "expected": True},
    {"name": "three_pages", "file": {"page_count": 3}, "expected": True},
    # 服务端读不出页数：**不是** 1 页，翻页器隐藏。
    {"name": "unknown_null", "file": {"page_count": None}, "expected": False},
    # 更旧的服务端响应没有这个字段：同样是未知。
    {"name": "missing_field", "file": {}, "expected": False},
    # 0 与负数不是可翻的页数（历史行里出现过 0）。
    {"name": "zero", "file": {"page_count": 0}, "expected": False},
    {"name": "negative", "file": {"page_count": -1}, "expected": False},
    # 没有文件对象（还没加载出来 / 没有原文）：不能抛，按未知处理。
    {"name": "no_file", "file": None, "expected": False},
]


@pytest.mark.skipif(NODE is None, reason="需要 node 才能驱动前端模块")
@pytest.mark.skipif(
    not (REPO_ROOT / "frontend" / "node_modules" / "esbuild").is_dir(),
    reason="需要 frontend/node_modules（esbuild）才能转译 JSX；先在前端目录 npm install",
)
def test_unknown_page_count_never_degrades_to_one():
    import os

    frontend = REPO_ROOT / "frontend"
    # 驱动与被转译的模块都写在 frontend/node_modules 下:node 的裸模块解析从
    # **文件所在目录**向上找 node_modules,写在 /tmp 里就找不到 esbuild / react。
    # 这个位置本身是 gitignored 的构建产物目录,不污染仓库。
    work = frontend / "node_modules" / f".tmp-page-count-{os.getpid()}"
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
                "HEALTHFLOW_TEST_CASES": json.dumps([case["file"] for case in CASES]),
            },
            cwd=frontend,
            timeout=180,
        )
        assert proc.returncode == 0, f"驱动失败:\n{proc.stdout}\n{proc.stderr}"
        actual = json.loads((work / "result.json").read_text(encoding="utf-8"))
        for case, got in zip(CASES, actual, strict=True):
            assert got is case["expected"], f"{case['name']}: 期望 {case['expected']}，得到 {got}"
    finally:
        shutil.rmtree(work, ignore_errors=True)
