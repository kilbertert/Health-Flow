"""映射表：本仓的副本与 genesis-evidence 的真值必须一致。

`app/service/condition_tags.py` 是被**有意复制**过来的一份表。复制会漂移——这个测试
就是拦住漂移的那道闸。任一侧改了名字而另一侧没跟，这里红。

**为什么是解析 Markdown 而不是让那边产出 JSON**：真值就是那份给商城侧看的文档，
让它再多一份机器可读的副本，等于把「漂移」从两处变成三处。解析一份格式稳定的表，
比维护一条跨仓产物管道便宜。
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from app.service.condition_tags import CONDITION_TAGS, label_pair_for

#: genesis-evidence 的 canonical 检出。它就在同一个工作区里，所以能直接读；
#: 不在一个工作区时跳过（CI、或 fork 出去的单仓检出），不失败。
GENESIS_DOC = Path("/home/claude/Projects/genesis-evidence/docs/condition-to-mall-tag.md")

_ROW = re.compile(
    r"^\|\s*`(?P<code>COND_[A-Z_]+)`\s*\|\s*(?P<cat>[^|]+?)\s*\|\s*(?P<name>[^|]+?)\s*\|\s*(?P<value>[^|]+?)\s*\|\s*$"
)


def _rows_from_doc() -> dict[str, tuple[str, str]]:
    rows: dict[str, tuple[str, str]] = {}
    for line in GENESIS_DOC.read_text(encoding="utf-8").splitlines():
        match = _ROW.match(line)
        if match:
            rows[match.group("code")] = (match.group("name"), match.group("value"))
    return rows


pytestmark = pytest.mark.skipif(
    not GENESIS_DOC.is_file(),
    reason="genesis-evidence 的映射表不在本工作区；跨仓一致性由那边的 CI 保证",
)


def test_every_row_in_the_doc_is_carried_here():
    """文档里的每一行都在本仓有对应，且（标签名, 取值）逐字相同。"""
    doc = _rows_from_doc()
    assert doc, "没解析到任何行——表的格式变了，正则要跟着改"

    missing = sorted(set(doc) - set(CONDITION_TAGS))
    assert not missing, f"文档里有、本仓没有的 condition_code：{missing}"

    drifted = {code: (CONDITION_TAGS[code], doc[code]) for code in doc if CONDITION_TAGS.get(code) != doc[code]}
    assert not drifted, f"两仓取值不一致（本仓 vs 文档）：{drifted}"


def test_we_carry_nothing_the_doc_does_not():
    """反向也要成立：本仓不能有文档里没有的行。

    只查一个方向时，本仓留下一条文档已删的旧映射不会红——而那条旧映射会继续
    把患者导到一件已经不该出现的商品上。
    """
    doc = _rows_from_doc()
    extra = sorted(set(CONDITION_TAGS) - set(doc))
    assert not extra, f"本仓有、文档没有的 condition_code：{extra}"


def test_unknown_condition_is_empty_not_a_fallback():
    """没有映射的健康方向返回空——**不是**「那就都给它」。"""
    assert label_pair_for("COND_NOT_A_REAL_CONDITION") is None


def test_the_pair_is_two_values_not_one():
    """标签名与取值是两个字段，即使本租户恰好同名。

    商城的标签是两层（`label_name` / `option_name`），取货按两者分别精确匹配。
    把它压成一个值，在下一个不同名的租户上就会静默取不到货。
    """
    name, value = CONDITION_TAGS["COND_HYPERTENSION_RISK"]
    assert name == "心血管功能评估"
    assert value == "心血管功能评估"
    assert isinstance((name, value), tuple) and len((name, value)) == 2
