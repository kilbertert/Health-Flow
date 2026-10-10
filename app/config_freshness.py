"""已加载的配置是否仍等于磁盘上那份 —— 一个信号，一种判定。

`EnvironmentFile=` 只在启动时被读一次：进程此后一直握着启动那一刻的取值。手工编辑 env
（不改代码、不触部署）之后，进程可能拿一份已经被取代的凭据继续工作。2026-10-10 的报告
全量失败就是这一形状：worker 起于 09-30 16:49，env 文件在 10-10 14:08:56 被替换，进程手里
的键对 provider 报 401，而磁盘上的键 200（#201）。

机制层的修法在 unit 上（`PartOf=` 传播 + `.path` 监听，见 ops/service-host/systemd/）。
本模块只管**看见**：机制若失效（unit 没装、重启失败、操作者手停了服务），
`/ready` 必须如实说 `stale`，而不是继续报 `configured`。

判定刻意只做一件事，且**不引入误报**：本进程的启动时刻早于 env 文件最后一次被写的时刻，
即为过期。它不检查内容、不解析键，所以不会被格式差异或时间戳精度骗到。
"""

from __future__ import annotations

import os
from datetime import UTC, datetime
from pathlib import Path

# 三种取值。`unknown` 不是失败，是**如实的「不知道」**：HEALTHFLOW_ENV_FILE 未设置时，
# 应用无从得知自己被哪份文件喂大，此时回答 unknown 比猜一个路径更诚实 —— 猜错方向会让
# 过期检测静默失效，而「静默失效」正是本模块要消除的东西。
FRESHNESS_CURRENT = "current"
FRESHNESS_STALE = "stale"
FRESHNESS_UNKNOWN = "unknown"

# 允许的时钟偏差（秒）。启动时刻与文件 mtime 都由内核给，同一台机器上的偏差只来自
# 文件系统的秒级粒度；给 1 秒余量，避免「启动后同一秒内文件被碰一下」被判成过期。
# 反过来说：真正的过期永远是一个明确的、跨越启动时刻的写入，不会被这个余量吃掉。
CLOCK_SKEW_SECONDS = 1.0


def process_started_at() -> datetime | None:
    """本进程的启动时刻。

    取 `/proc/<pid>` 目录的 mtime，它与 `/proc/<pid>/stat` 第 22 字段推出的启动时刻在
    实测中相差 6 毫秒 —— 不需要 psutil，也不该为这一件事引入依赖。

    `/proc` 不存在（非 Linux）时返回 None，由调用方降级为 unknown。这是**如实的不确定**，
    不是伪装成 current。
    """
    try:
        return datetime.fromtimestamp(os.stat(f"/proc/{os.getpid()}").st_mtime, tz=UTC)
    except OSError:
        return None


def config_freshness(env_file: str) -> tuple[str, datetime | None]:
    """返回 (取值, env 文件的 mtime)。

    取值是 `current` / `stale` / `unknown` 之一；mtime 仅在能读到文件时有值，供 /ready 一并
    透出，让「为什么判成 stale」不需要再登录主机去问。
    """
    started = process_started_at()
    if not env_file.strip() or started is None:
        return FRESHNESS_UNKNOWN, None
    try:
        changed_at = datetime.fromtimestamp(Path(env_file).stat().st_mtime, tz=UTC)
    except OSError:
        # 声明了路径却读不到：这是一处配置错误，不是「新鲜」。报 unknown 而不是 stale ——
        # 我们确实不知道磁盘上有什么。但也不报 current：那会是一句没有依据的断言。
        return FRESHNESS_UNKNOWN, None
    if (changed_at - started).total_seconds() > CLOCK_SKEW_SECONDS:
        return FRESHNESS_STALE, changed_at
    return FRESHNESS_CURRENT, changed_at
