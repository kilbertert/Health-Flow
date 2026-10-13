"""「值这一类原因」在两侧是**同一份判据**（#204 的复审修复）。

准入结论要等评估之后才有，所以确认页的判断留在前端 —— 但它用的是**同一个问题的服务端
名字**，不是第二套解析规则。这条约束此前只有注释在说（`admissionText.js` 写着「与
`admission.value_reason` 同一条判据」），而它**已经被违反过一次**：前端那一处少写了最后
一条出口（`exactly one number → None`），于是 `3.63 mmol/L` 这种好值在确认页被判成坏值，
患者被挡在一个服务端认为没问题的行前面；同时全角数字（`１.２`）在两边读不出同一个答案。

注释不能承担这条约束 —— 一条能被注释说出口的规则，也会被注释之外的改动推翻。所以这里把
两侧喂同一组输入、**逐条比对**：任何一侧改了判据而另一侧没跟上，这条就红。

先例：`tests/test_admission.py` 的「两侧同名」断言同源，但它只覆盖值这一类的一部分形态；
本文件补的是**跨语言**的那一层，含全角数字、千分位、指数记法这些一改就会分叉的边界。
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from app.service.admission import value_level_reason, value_only_reason, value_reason

REPO_ROOT = Path(__file__).resolve().parents[1]
MODULE = REPO_ROOT / "frontend" / "src" / "admissionText.js"
NODE = shutil.which("node")

# 一组输入，每一类边界各来几个。它们不是随手凑的：每一条都对应一个**改法不同**的形态。
_CASES = [
    # 一个数 —— 两侧都必须说「没有问题」（前端曾经在这里漏掉最后一条出口）。
    "3.63",
    "3.63 mmol/L",
    "76.1kg",
    "0.81",
    "-1.5",
    "１２",
    "１.２",
    # 两个数 —— 患者要选一个。
    "3.39 / 3.63",
    "3.39/3.63",
    "1.53 1.50",
    "5.4 / 5.5",
    "76.1kg 83.6kg",
    "3.87 4.00",
    "３.６３ ４.００",
    "1.4 1.5 mmol/L",
    # 带符号 —— 哪怕含两个数字，也不是「用哪个」。
    "<3 x 10^6/L",
    "<0.01",
    "> 1.00",
    ">=90",
    "≤5",
    # 单位/时间被抽进了值 —— 恰好两个数字，但那两个数之间没有可做的选择。
    "0 x 10^6/L",
    "10:00",
    "7:30",
    "1 x 10^9/L",
    # 千分位与指数记法 —— 不能读成两个值（那会给出两个**错的**候选）。
    "1,234",
    "1e3",
    "2.5e-3",
    # 定性项的词 —— 值坏了，不是两个值。
    "Nil",
    "Negative",
    "Not Detected",
    "Clear",
    "AB Rh(D) POSITIVE",
    # 空。
    "",
    "   ",
    # 三个及以上的数字 —— 没有「选一个」这个问题。
    "3.5 4.5 5.5",
    "1.2.3",
    "1,234.5",
]

_DRIVER = """
import fs from 'node:fs';
import process from 'node:process';
import { pathToFileURL } from 'node:url';
import { build } from 'esbuild';
const outfile = process.env.OUTFILE;
await build({
  entryPoints: [process.env.MODULE],
  outfile,
  bundle: true,
  packages: 'external',
  format: 'esm',
  logLevel: 'silent',
});
const mod = await import(pathToFileURL(outfile).href);
const cases = JSON.parse(process.env.CASES);
const out = {};
for (const value of cases) {
  // 与确认页面对的那个时机一致：准入结论还没有值，走「反推」那一支。
  out[value] = mod.valueReasonOrNull({
    metric_value: value,
    admission_reason: null,
    inferred_abnormal_flag: null,
    abnormal_flag: 'H',
  });
  out[`${value}\\u0000names`] = mod.dualValues({ metric_value: value });
}
// 「报告把判据印成同一个界」那一类（#235）：值与参考范围两栏都给，两侧各判一次。
for (const [key, row] of Object.entries(JSON.parse(process.env.BOUND_CASES))) {
  out[`bound:${key}`] = mod.valueReasonOrNull({
    metric_value: row.value,
    reference_range: row.reference,
    unit: row.unit,
    admission_reason: null,
    inferred_abnormal_flag: null,
    abnormal_flag: 'H',
  });
}
fs.writeFileSync(process.env.RESULT, JSON.stringify(out));
"""

# 「报告把判据印成同一个界」（#235）—— 每一行同时喂给 `value_level_reason`（服务端）与
# `valueReasonOrNull`（确认页）。`expected` 是**其中一侧**的答案，另一侧必须相同：
# 断言的是两侧相等，所以往任何一侧加一条出口或换个顺序都会红。
_BOUND_CASES = {
    # 同一个界 → 判得出（None），患者不必修正一个报告上根本没有的精确值。
    "same_bound": {"value": "<20", "reference": "(<20)", "unit": "IU/ml"},
    "space_and_full_width": {"value": "≤ 20", "reference": "(<=20)", "unit": "IU/ml"},
    # 界之后还有别的东西 —— 不是「这一格是一个界」这回事。
    "tail_number": {"value": "<20 5.2", "reference": "(<20)", "unit": "IU/ml"},
    "tail_other_unit": {"value": "<20 mmol/L", "reference": "(<20)", "unit": "IU/ml"},
    # 方向不同、数值不同 —— 报告没有把同一句话印在两处。
    "other_direction": {"value": "<20", "reference": "(>20)", "unit": "IU/ml"},
    "other_number": {"value": "<2.6", "reference": "<=1.8", "unit": "mmol/L"},
    # 参考范围里没有界（AIP）—— 影射到哪个阈值是医学判断，不猜。
    "no_bound_in_reference": {"value": "<0.01", "reference": None, "unit": None},
    # 界**之前**还有一个测量值 —— `>20` 只是它旁边的一句话，不是「这一格是一个界」。
    # 评审在 #237 指出 `search` 会把尾巴当成界（`40 >20` 配 `>20` 被判成正常）。
    "prefix_measurement": {"value": "40 >20", "reference": "(>20)", "unit": None},
    # 参考范围里有**两个**判据（`>20, <30`）—— 光看一个就下「正常」会在另一个上错。
    "two_reference_bounds": {"value": "<20", "reference": "(>20, <30)", "unit": None},
}

needs_node = [
    pytest.mark.skipif(NODE is None, reason="需要 node 才能驱动前端模块"),
    pytest.mark.skipif(
        not (REPO_ROOT / "frontend" / "node_modules" / "esbuild").is_dir(),
        reason="需要 frontend/node_modules（esbuild）；先在前端目录 npm install",
    ),
]


def _frontend_reasons(
    cases: list[str],
    bound_cases: dict[str, dict[str, str | None]] | None = None,
) -> dict[str, str | None]:
    frontend = REPO_ROOT / "frontend"
    work = frontend / "node_modules" / f".tmp-agree-{os.getpid()}"
    shutil.rmtree(work, ignore_errors=True)
    work.mkdir(parents=True)
    try:
        driver = work / "driver.mjs"
        driver.write_text(_DRIVER, encoding="utf-8")
        result = work / "result.json"
        proc = subprocess.run(
            [NODE, str(driver)],
            capture_output=True,
            text=True,
            env={
                **os.environ,
                "MODULE": str(MODULE),
                "OUTFILE": str(work / "admission.mjs"),
                "CASES": json.dumps(cases),
                "BOUND_CASES": json.dumps(bound_cases or {}),
                "RESULT": str(result),
            },
            cwd=frontend,
            timeout=180,
        )
        assert proc.returncode == 0, f"驱动失败:\n{proc.stdout}\n{proc.stderr}"
        return json.loads(result.read_text(encoding="utf-8"))
    finally:
        shutil.rmtree(work, ignore_errors=True)


@needs_node[0]
@needs_node[1]
def test_both_sides_give_the_same_reason_for_every_shape():
    """两侧对**每一个**形态给出同一个答案 —— 断言的是相等，不是各自等于某个字面量。

    相等这条比「各自正确」强：往一侧加一条出口、或在一侧换个顺序，都会红。
    """
    front = _frontend_reasons(_CASES)
    # 确认页的反推只到**值这一层**（没有参考范围），所以基准是 `value_only_reason` 而不是
    # `value_reason`：带符号（#235）与分类结果词（#236）两条出口就在那一层里，两侧都要有。
    # 拿裸 `value_reason` 当基准会让这两条出口在服务端"多出来"、判成分叉 —— 那是本断言
    # 该有的敏感度，不是它的错。
    server = {value: value_only_reason(value, None) for value in _CASES}
    mismatched = {
        value: {"server": server[value], "frontend": front[value]}
        for value in _CASES
        if server[value] != front[value]
    }
    assert mismatched == {}, f"两侧对同一行的判定分叉了：{json.dumps(mismatched, ensure_ascii=False)}"


@needs_node[0]
@needs_node[1]
def test_both_sides_offer_the_same_candidates_for_a_two_value_row():
    """判成「两个值」时，两侧给出的候选**逐字相同且保留原文写法**。

    候选是页面上的按钮文字，也是提交时那个修正值 —— 一侧把 `4.00` 读成 `4`（或读成
    `["1","234"]`）就会让患者提交一个报告上没写过的数。
    """
    from app.service.admission import _value_number_re

    front = _frontend_reasons(_CASES)
    two_valued = [value for value in _CASES if value_reason(value) == "two_values"]
    assert two_valued, "这组用例必须至少覆盖一个「两个值」的形态"
    for value in two_valued:
        server = _value_number_re.findall(value)
        assert front[f"{value}\u0000names"] == server, value
        # 原文字面量（`4.00` 不是 `4`）—— 这正是「保留原文写法」那一条要钉的东西：
        # 归一化成数字再格式化会让 `76.1kg` 变成 `76.1`、`３.６３` 变成 `3.63`。
        assert server == [part for part in server], value


@needs_node[0]
@needs_node[1]
def test_both_sides_agree_on_a_signed_value_whose_bound_is_the_reports_criterion():
    """`<20` 配 `(<20)` 这一类，两侧给同一个答案（#235）。

    与上面那条同一个道理：准入结论要等评估之后才有，所以确认页必须**在患者提交之前**
    就说明白这一行判不判得出。两侧的判据住在一处（`signed_value_reason` /
    `signedBoundIsTheCriterion`），这条断言两侧相等 —— 任何一侧漏掉「界之后只剩单位」
    或「方向/数值必须相同」这些出口，它都会红。
    """
    front = _frontend_reasons([], _BOUND_CASES)
    mismatched = {}
    for key, row in _BOUND_CASES.items():
        server = value_level_reason(row["value"], row["reference"], row["unit"])
        if server != front[f"bound:{key}"]:
            mismatched[key] = {"server": server, "frontend": front[f"bound:{key}"]}
    assert mismatched == {}, f"带符号值的判定两侧分叉：{json.dumps(mismatched, ensure_ascii=False)}"

    # 守卫要能红：同一个界那一侧必须真的给出 None（判得出），否则上面那条是空断言。
    assert front["bound:same_bound"] is None
    assert front["bound:no_bound_in_reference"] == "invalid_value"


@needs_node[0]
@needs_node[1]
def test_the_guard_can_fail():
    """守卫本身要能红：拿一份**故意改坏**的候选集合断言它拒绝。

    先例：本仓库被评审指出过「守卫看起来在守、其实永远不会红」两次。一条只验证「相等」
    的断言如果两侧其实都返回同一个常量，它也不会红 —— 这里用一个不可能相等的输入钉住
    「它真的在读两侧的输出」。
    """
    front = _frontend_reasons(["3.39 / 3.63"])
    assert front["3.39 / 3.63"] == value_reason("3.39 / 3.63") == "two_values"
    assert front["3.39 / 3.63"] != value_reason("1,234"), "守卫必须真的在读两侧的输出"
