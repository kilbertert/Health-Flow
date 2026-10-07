"""`valueUnusable` 谓词(异常判定收敛后的前端守卫)。

本文件驱动的是**真实的前端模块**:node 导入 `frontend/src/pages/Upload.jsx`
导出的 `valueUnusable`,断言的对象与浏览器里渲染用的是同一份代码(先例:
`tests/test_e2e_python_resolver.py` 用同样的方式驱动 `frontend/e2e/python.mjs`)。

回归的是 #138 复审指出的两条:

1. 患者**排除**的指标,服务端同样返回空判定,但它的值完全可解析 —— 不能被标成
   「数值无法识别为单个数字」。这一条在 e2e 里够不到:确认后前端会用真实数据
   重新拉取报告(而确认链路需要商城票据与证据服务,e2e 环境没有),mock 出来的
   响应立刻被覆盖。
2. 字段出现之前的响应(契约向后兼容)缺 `inferred_abnormal_flag` 时,可解析的
   多值异常仍必须被拦下 —— 否则用户以为确认过了,后端却整行丢掉。
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
MODULE = REPO_ROOT / "frontend" / "src" / "pages" / "Upload.jsx"

NODE = shutil.which("node")

# 用 vite 的 esbuild 转译 JSX（node 直接 import .jsx 会卡在 JSX 语法上），
# 再从一个临时目录里导入 —— 该目录没有 node_modules，react 能正常解析到前端的依赖。
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
  JSON.stringify(JSON.parse(process.env.HEALTHFLOW_TEST_CASES).map((m) => mod.valueUnusable(m))),
);
"""

CASES = [
    # 患者排除:判定为空,但值可解析 —— 不是「用不了」。
    {"name": "excluded", "metric": {"abnormal_flag": "H", "metric_value": "2.3", "confirmation_status": "excluded",
                                     "inferred_abnormal_flag": None}, "expected": False},
    # 多值异常:判定为空,值解析不出一个数 —— 用不了。
    {"name": "multi_value", "metric": {"abnormal_flag": "H", "metric_value": "3.87 4.00",
                                        "inferred_abnormal_flag": None}, "expected": True},
    # 已确认的正常指标:判定为 N —— 不是用不了。
    {"name": "normal", "metric": {"abnormal_flag": "N", "metric_value": "138",
                                   "inferred_abnormal_flag": "N"}, "expected": False},
    # 判定为 H:值可用,正常参与解读。
    {"name": "abnormal", "metric": {"abnormal_flag": "H", "metric_value": "6.9",
                                     "inferred_abnormal_flag": "H"}, "expected": False},
    # 旧响应(字段缺失):没有判定字段就无从知道值能不能用,**保守返回 false**
    # (宁可少提示,不可误报——把可解析的异常挡在确认之外更糟)。
    {"name": "legacy_flag_no_range", "metric": {"abnormal_flag": "H", "metric_value": "5.2"}, "expected": False},
    {"name": "legacy_flag_multi_value",
     "metric": {"abnormal_flag": "H", "metric_value": "3.87 4.00"}, "expected": False},
    # 旧响应 + 模型标 N → 不是用不了。
    {"name": "legacy_normal", "metric": {"abnormal_flag": "N", "metric_value": "138"}, "expected": False},
]


@pytest.mark.skipif(NODE is None, reason="需要 node 才能驱动前端模块")
def test_value_unusable_follows_the_server_decision():
    import os
    import shutil as _shutil

    frontend = REPO_ROOT / "frontend"
    # 驱动与被转译的模块都写在 frontend/node_modules 下:node 的裸模块解析从
    # **文件所在目录**向上找 node_modules,写在 /tmp 里就找不到 esbuild / react。
    # 这个位置本身是 gitignored 的构建产物目录,不污染仓库。
    work = frontend / "node_modules" / f".tmp-value-unusable-{os.getpid()}"
    _shutil.rmtree(work, ignore_errors=True)
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
                "HEALTHFLOW_TEST_CASES": json.dumps([case["metric"] for case in CASES]),
            },
            cwd=frontend,
            timeout=180,
        )
        assert proc.returncode == 0, f"驱动失败:\n{proc.stdout}\n{proc.stderr}"
        actual = json.loads((work / "result.json").read_text(encoding="utf-8"))
        for case, got in zip(CASES, actual, strict=True):
            assert got is case["expected"], f"{case['name']}: 期望 {case['expected']}，得到 {got}"
    finally:
        _shutil.rmtree(work, ignore_errors=True)
