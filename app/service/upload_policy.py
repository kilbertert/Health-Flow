"""上传策略：服务端对患者侧公布的**唯一**一份受理规则。

前端要在本地做即时 UX（`accept` 属性、剩余份数、超出提示、粘贴分类），但它不该
自己记一份规则 —— 今天 `MAX_UPLOAD_FILES = 20` 在 `Upload.jsx` 里被写死三处
（剩余额度、提示文案、`maxCount`），受理扩展名另有一套，运维者调大配置前端**不会**
跟着变。策略由这里产出、经只读端点下发，前端只在拿不到时用内建默认值兜底。

**只下发受理规则本身**：扩展名与三个上限。服务端路径、目录、解析配置一律不出域。
"""

from __future__ import annotations

from app.config import get_settings
from app.service.report_material import ACCEPTED_EXTENSIONS


def upload_policy() -> dict[str, object]:
    """当前生效的上传策略。键集合固定，多一个都是出域（有契约测试钉住）。"""
    settings = get_settings()
    return {
        # 受理扩展名来自类型判定模块 —— 那里是「什么算受理类型」的唯一权威。
        "accepted_extensions": sorted(ACCEPTED_EXTENSIONS),
        "max_files": int(settings.MAX_UPLOAD_FILES),
        "max_file_bytes": int(settings.MAX_UPLOAD_BYTES),
        "max_total_bytes": int(settings.MAX_UPLOAD_TOTAL_BYTES),
    }
