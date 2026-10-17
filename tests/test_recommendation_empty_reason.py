"""推荐为空的原因由服务端单点给出（GLOSSARY.md 的「推荐为空的原因」，#246）。

收敛前「本次推荐为什么是空的」有**四份**答案：服务端契约三种、前端自造第四种
（`session_expired`，靠 `/[0-9]{3}/` 从人话里捞 HTTP 状态）。本文件钉住三件事：

1. **一处声明**：三组取值只在一个模块里声明，其它地方（含商城模块的子集）由它派生。
2. **不存在服务端产生不了的取值**：前端能说的每一个原因都必须落在那份词表里 ——
   这是 #226「成因不再由文本嗅探」的同一形状。
3. **访问失败由服务端具名**：401/404 的成因随响应头出域，前端读结构化事实，
   不再从 `detail` 那句人话里猜。
"""

from __future__ import annotations

import ast
import json
import os
import shutil
import subprocess
from pathlib import Path
from typing import get_args

import pytest

from app.service.recommendation_empty_reason import (
    CART_LINK_REASONS,
    CART_LINK_REASONS_ORDERED,
    MALL_GOODS_REASONS_ORDERED,
    RECOMMENDATION_REASONS,
    RECOMMENDATION_REASONS_ORDERED,
    REPORT_ACCESS_BY_STATUS,
    REPORT_ACCESS_REASONS,
    REPORT_ACCESS_REASONS_ORDERED,
    vocabulary,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
APP = REPO_ROOT / "app"
FRONTEND = REPO_ROOT / "frontend" / "src"
NODE = shutil.which("node")


# ── 1. 一处声明 ────────────────────────────────────────────────────────────


def test_the_three_groups_are_disjoint_and_complete():
    """三组取值各自为本、两两不相交：它们回答的是三个不同的问题。"""
    assert RECOMMENDATION_REASONS.isdisjoint(CART_LINK_REASONS)
    assert RECOMMENDATION_REASONS.isdisjoint(REPORT_ACCESS_REASONS)
    assert CART_LINK_REASONS.isdisjoint(REPORT_ACCESS_REASONS)
    assert vocabulary() == RECOMMENDATION_REASONS | CART_LINK_REASONS | REPORT_ACCESS_REASONS
    assert len(vocabulary()) == len(RECOMMENDATION_REASONS_ORDERED) + len(CART_LINK_REASONS_ORDERED) + len(
        REPORT_ACCESS_REASONS_ORDERED
    )


def test_the_contract_and_the_mall_subset_both_derive_from_the_one_declaration():
    """契约与商城子集都由那份声明派生，不各自抄一遍。"""
    from app.schema.report import CartLinkResponse, RecommendationReason
    from app.service.mall_goods import Reason

    assert set(get_args(RecommendationReason)) == RECOMMENDATION_REASONS
    assert set(get_args(Reason)) == set(MALL_GOODS_REASONS_ORDERED)
    assert set(get_args(Reason)) < RECOMMENDATION_REASONS, "商城的子集必须真包含在端点词表里"
    # 注解是 `CartLinkReasonLiteral | None` —— 取出里面那个 Literal 的取值再比。
    annotation = CartLinkResponse.model_fields["reason"].annotation
    inner = next(arg for arg in get_args(annotation) if get_args(arg))
    assert set(get_args(inner)) == CART_LINK_REASONS


def test_no_module_restates_a_group_of_these_values():
    """**没有第二处**声明这几组取值（同 #183 对准入词表的守卫）。

    判据：模块级对 `Literal[...]` 的赋值里，出现两个以上属于本词表的字面量 —— 那是在
    重新声明一份词表，而不是引用它。
    """
    offenders: list[str] = []
    for path in sorted(APP.rglob("*.py")):
        if path.name == "recommendation_empty_reason.py":
            continue  # 词表的家
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if not (isinstance(node, ast.Assign) and isinstance(node.value, ast.Subscript)):
                continue
            if not (isinstance(node.value.value, ast.Name) and node.value.value.id == "Literal"):
                continue
            slice_node = node.value.slice
            items = slice_node.elts if isinstance(slice_node, ast.Tuple) else [slice_node]
            # 只数**字面量**（派生写法 `Literal[*X]` 是 Starred，不算重声明）。
            literals = {i.value for i in items if isinstance(i, ast.Constant)}
            if len(literals & vocabulary()) >= 2:
                offenders.append(f"{path.relative_to(REPO_ROOT)}:{node.lineno}")
    assert not offenders, f"这些位置重新声明了一份原因词表：{offenders}"


# ── 2. 服务端产生不了的取值不存在 ──────────────────────────────────────────

_MODAL = FRONTEND / "components" / "Recommendations.jsx"


def _strip_comments(source: str) -> str:
    """去掉 `//` 行注释与 `/* */` 块注释后的源码。

    守卫要判的是**代码**。这些文件里保留着「删掉的是什么、为什么」的注释，那是这个仓库
    的写法；把注释一起判红会逼着下一次改动删掉解释，而不是删掉缺陷。
    """
    import re

    without_block = re.sub(r"/\*.*?\*/", "", source, flags=re.DOTALL)
    return "\n".join(line.split("//", 1)[0] for line in without_block.splitlines())

_DRIVER = """
import fs from 'node:fs';
import path from 'node:path';
import process from 'node:process';
import { pathToFileURL } from 'node:url';
import { build } from 'esbuild';

const outfile = path.join(process.env.HEALTHFLOW_TEST_OUTDIR, 'recs.mjs');
await build({
  entryPoints: [process.env.HEALTHFLOW_TEST_MODULE],
  outfile, bundle: true, packages: 'external', format: 'esm', jsx: 'automatic', logLevel: 'silent',
});
const mod = await import(pathToFileURL(outfile).href);
fs.writeFileSync(
  path.join(process.env.HEALTHFLOW_TEST_OUTDIR, 'result.json'),
  JSON.stringify({
    emptyReasons: Object.keys(mod.EMPTY_REASONS),
    accessReasons: Object.keys(mod.ACCESS_REASONS),
    cartLinkReasons: Object.keys(mod.CART_LINK_REASONS),
  }),
);
"""


def _drive_frontend() -> dict:
    frontend = REPO_ROOT / "frontend"
    work = frontend / "node_modules" / f".tmp-reasons-{os.getpid()}"
    shutil.rmtree(work, ignore_errors=True)
    work.mkdir(parents=True)
    try:
        driver = work / "driver.mjs"
        driver.write_text(_DRIVER, encoding="utf-8")
        proc = subprocess.run(
            [NODE, str(driver)],
            capture_output=True,
            text=True,
            env={**os.environ, "HEALTHFLOW_TEST_MODULE": str(_MODAL), "HEALTHFLOW_TEST_OUTDIR": str(work)},
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
    reason="需要 frontend/node_modules（esbuild）才能转译 JSX",
)
def test_the_front_end_can_only_say_reasons_the_server_can_produce():
    """**负控**：患者侧可用取值集 ⊆ 服务端出域取值集。

    改动前 `EMPTY_REASONS` 里有第四种 `session_expired` —— 一个服务端**从不产生**的取值，
    所以它既不会被契约测试看见，也不会随词表改动而失效。这条断言让那种取值不可能再加进来。
    """
    keys = _drive_frontend()
    for group, allowed in (
        ("emptyReasons", RECOMMENDATION_REASONS | REPORT_ACCESS_REASONS),
        ("accessReasons", REPORT_ACCESS_REASONS),
        ("cartLinkReasons", CART_LINK_REASONS),
    ):
        assert set(keys[group]) <= allowed, f"{group} 里有服务端产生不了的取值：{set(keys[group]) - allowed}"

    # 而且每一句都真的覆盖到了（少写一个会落到兜底文案，患者得到一句说不清的话）。
    assert set(keys["emptyReasons"]) == RECOMMENDATION_REASONS
    assert set(keys["cartLinkReasons"]) == CART_LINK_REASONS


def test_the_session_failure_message_points_at_the_mall_entry_not_a_login_page():
    """会话失效那句必须指向**商城入口** —— 本仓没有登录页（#172）。

    改动前它说「请重新登录后查看推荐」，指向一个不存在的页面；服务端在同一情形下说的是
    「请从商城入口重新进入」。同一个事实，患者侧两句话，其中一句是错的。
    """
    code = _strip_comments(_MODAL.read_text(encoding="utf-8"))
    assert "重新登录" not in code, "「重新登录」指向一个不存在的入口"
    assert "商城入口" in code, "会话失效的话必须与服务端同向"


def test_the_front_end_no_longer_sniffs_http_status_out_of_a_message():
    """**静态守卫**：不再用正则从消息里提取 HTTP 状态码。

    先例：#226 的「成因不再由文本嗅探」、#135 的「前端不再推导」。判据是那一段特征写法
    （`/\\b(401|404)\\b/` 一类）不再出现。
    """
    # 判据只看**代码**，不看注释：这段注释本身要能说明「删掉的是什么」，
    # 而一个把解释自己的话也判红的守卫只会教人把注释删掉。
    code = _strip_comments(_MODAL.read_text(encoding="utf-8"))
    assert "401|404" not in code, "仍在用正则从人话里嗅探 HTTP 状态"
    assert "regex" not in code and ".test(err.message" not in code, "仍在从消息文本里提取状态"


def test_the_storefront_failure_path_uses_the_servers_named_reason():
    """加购失败读服务端的 `reason`，而不是一句一概而论的兜底。"""
    source = _MODAL.read_text(encoding="utf-8")
    assert "CART_LINK_REASONS[link.reason]" in source, "加购失败没有用服务端给的原因"
    assert "report_not_ready" in source and "link_unavailable" in source


def test_the_front_end_never_invents_a_reason_for_a_missing_report_id():
    """`!reportId` 不再伪造 `no_published_card` —— 报告都没打开，谈不上「没有风险」。"""
    source = _MODAL.read_text(encoding="utf-8")
    head = source.split("useEffect", 1)[1] if "useEffect" in source else source
    assert "reason: 'no_published_card'" not in head, "仍在为未打开的报告伪造服务端成因"


# ── 3. 访问失败由服务端具名 ────────────────────────────────────────────────


def test_the_two_report_level_access_outcomes_have_their_own_names():
    """401 与会话失效、404 与报告读不到 —— 同一个事实在服务端只有一个名字。"""
    assert REPORT_ACCESS_BY_STATUS[401] == "session_expired"
    assert REPORT_ACCESS_BY_STATUS[404] == "report_not_found"
    assert set(REPORT_ACCESS_BY_STATUS.values()) == REPORT_ACCESS_REASONS


def test_the_error_carries_the_named_cause_not_only_a_sentence():
    """`api.js` 把结构化事实（状态码与成因头）留给调用方。

    改动前 `parseError` 只返回那句话，状态码被丢掉 —— 于是调用方只能从文本里把它嗅探
    回来，而归因会随 `detail` 的措辞静默翻转。
    """
    source = (FRONTEND / "api.js").read_text(encoding="utf-8")
    assert "X-Report-Access-Reason" in source, "成因头没有被读"
    assert "error.accessReason" in source
    assert "error.status" in source, "状态码被丢掉了"

    api_module = FRONTEND / "api.js"
    driver = REPO_ROOT / "frontend" / "node_modules" / f".tmp-apierr-{os.getpid()}"
    if NODE is None or not (REPO_ROOT / "frontend" / "node_modules" / "esbuild").is_dir():
        pytest.skip("需要 node + esbuild")
    shutil.rmtree(driver, ignore_errors=True)
    driver.mkdir(parents=True)
    try:
        (driver / "driver.mjs").write_text(
            """
import fs from 'node:fs';
import path from 'node:path';
import process from 'node:process';
import { pathToFileURL } from 'node:url';
import { build } from 'esbuild';
const outfile = path.join(process.env.OUT, 'api.mjs');
await build({ entryPoints: [process.env.MOD], outfile, bundle: true, packages: 'external',
  format: 'esm', logLevel: 'silent' });
const mod = await import(pathToFileURL(outfile).href);
const res = {
  status: 404,
  headers: { get: (name) => (name.toLowerCase() === 'x-report-access-reason' ? 'report_not_found' : null) },
  json: async () => ({ detail: "报告不存在" }),
  ok: false,
};
const err = await mod.parseError(res);
fs.writeFileSync(path.join(process.env.OUT, 'result.json'), JSON.stringify({
  status: err.status, accessReason: err.accessReason, message: err.message,
}));
""",
            encoding="utf-8",
        )
        proc = subprocess.run(
            [NODE, str(driver / "driver.mjs")],
            capture_output=True,
            text=True,
            env={
                **os.environ,
                "MOD": str(api_module),
                "OUT": str(driver),
            },
            cwd=REPO_ROOT / "frontend",
            timeout=180,
        )
        assert proc.returncode == 0, f"驱动失败:\n{proc.stdout}\n{proc.stderr}"
        result = json.loads((driver / "result.json").read_text(encoding="utf-8"))
    finally:
        shutil.rmtree(driver, ignore_errors=True)

    assert result["accessReason"] == "report_not_found"
    assert result["status"] == 404
    assert result["message"] == "报告不存在", "人话仍然照旧出域（成因与文案各自为本）"
