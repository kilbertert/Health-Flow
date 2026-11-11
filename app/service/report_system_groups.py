"""报告指标的系统分组装配（#294）—— `system_groups` 的唯一产生处。

把「指标 → 器官/系统」审查映射（`metric_system_map`，#292 冻结的声明）装配成
报告响应里的分组，供患者侧按系统渲染。判据：

- **归组键与证据门禁同源**：先取指标落定的 `metric_code`；为空且目录可用时用
  `resolve_metric_code` 重裁决（与证据桥同一优先级、同一条「目录不可用就不猜」）。
  落定码查无映射、或解析不出 canonical 码 → 归 `other`，不猜归属。
- **无检测数据的系统不出现**：组里没指标就不产出 —— 不宣称器官健康。
- **没有组级状态**：组只带成员名单（`metric_ids`）与计数。同组「异常」与「待核对」
  并存时两者都在计数里；`metric_ids` 即原文回链（每条指标的 `evidence_text` /
  `page_number` / `source_id` 随响应出域，分组不复制证据）。
- 患者排除的行**仍在** `metric_ids` 里（它仍在这份报告上），计数口径照「指标确认
  决策」不计它 —— 与全报告计数同一个函数。
"""

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

from app.service.evidence_bridge import resolve_metric_code
from app.service.metric_system_map import (
    METRIC_SYSTEM_MAP,
    OTHER_GROUP_KEY,
    SYSTEM_GROUPS,
)
from app.service.report_metric_counts import MetricCounts, metric_counts


@dataclass(frozen=True)
class SystemGroup:
    """一个系统的分组投影：成员名单 + 该组的计数口径（没有组级状态）。"""

    key: str
    name: str
    metric_ids: list[int]
    counts: MetricCounts


def _group_key(metric: Any, catalog: Iterable[str] | None) -> str:
    code = getattr(metric, "metric_code", None)
    if not code and catalog is not None:
        code = resolve_metric_code(getattr(metric, "metric_name", "") or "", catalog)
    return METRIC_SYSTEM_MAP.get(code, OTHER_GROUP_KEY) if code else OTHER_GROUP_KEY


def system_groups(metrics: list[Any], *, catalog: Iterable[str] | None = None) -> list[SystemGroup]:
    """把指标行按唯一映射装配成分组列表（顺序 = 映射声明序，`other` 恒在最后）。

    输入行序保留在组内 `metric_ids` 里（调用方按既有口径排序后传入）。
    """
    buckets: dict[str, list[Any]] = {}
    for metric in metrics:
        buckets.setdefault(_group_key(metric, catalog), []).append(metric)
    groups: list[SystemGroup] = []
    for key, name in SYSTEM_GROUPS:
        rows = buckets.get(key)
        if not rows:
            continue
        groups.append(
            SystemGroup(
                key=key,
                name=name,
                metric_ids=[row.id for row in rows],
                counts=metric_counts(rows),
            )
        )
    return groups
