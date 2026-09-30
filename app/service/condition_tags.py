"""`condition_code` → 商城（标签名，标签值）。

**这是一份被有意复制过来的表。** 真值在 genesis-evidence 的
`docs/condition-to-mall-tag.md`；那份文档是交付给商城侧的词汇表，本仓只消费它的结果。
复制会漂移——所以这里有一个**契约测试**：`tests/test_condition_tags.py` 逐条比对两仓
的取值，任一侧改了名字而另一侧没跟，测试就红。漂移由测试拦住，不靠人记得。

表本身是**运行期读回来的**（2026-09-30）：12 个取值在商城侧逐个查到对应行，
其中 2 个在真实取货上跑通过。三层结构实测为「类别名 ≠ 标签名 = 取值」——
**取货只用（标签名, 取值）两层**，类别名在这里没有位置。

未列出的 `condition_code` 返回空元组，调用方据此降级为「暂无推荐」。**空是对的**：
把该租户全部商品推给任意健康风险，正是这道闸门要防的事。
"""

from __future__ import annotations

#: 取值来源见模块文档串。每行 `condition_code: (标签名, 取值)`。
#:
#: 标签名与取值在本租户是**同名**的（12/12），但两者是不同的字段（`goods_label.label_name`
#: 与 `goods_label_option.option_name`），商城按它们分别精确匹配——所以这里照实写两份，
#: 不省成一个字段：下一批租户的命名不保证还同名。
CONDITION_TAGS: dict[str, tuple[str, str]] = {
    "COND_HYPERTENSION_RISK": ("心血管功能评估", "心血管功能评估"),
    "COND_PREDIABETES": ("糖代谢异常风险评估", "糖代谢异常风险评估"),
    "COND_DYSLIPIDEMIA": ("血脂异常风险评估", "血脂异常风险评估"),
    "COND_MASLD_RISK": ("脂肪肝风险评估", "脂肪肝风险评估"),
    "COND_HYPERURICEMIA_RISK": ("尿酸管理评估", "尿酸管理评估"),
    "COND_CKD_RISK": ("肾脏功能评估", "肾脏功能评估"),
    "COND_ANEMIA_PATTERN": ("贫血与缺铁评估", "贫血与缺铁评估"),
    "COND_VITAMIN_D_DEFICIENCY": ("维生素 D 缺乏评估", "维生素 D 缺乏评估"),
    "COND_OSTEOPOROSIS_RISK": ("骨质疏松症风险评估", "骨质疏松症风险评估"),
    "COND_SARCOPENIA_FRAILTY": ("肌少症风险评估", "肌少症风险评估"),
    "COND_MALNUTRITION_RISK": ("营养状态评估", "营养状态评估"),
    "COND_CHRONIC_CONSTIPATION": ("肠道健康评估", "肠道健康评估"),
}


def label_pair_for(condition_code: str) -> tuple[str, str] | None:
    """该健康方向对应的（标签名，取值）；没有映射时返回 `None`。"""
    return CONDITION_TAGS.get(condition_code)
