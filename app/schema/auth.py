"""会话与报告历史 schema。

账号体系的请求模型（注册/登录/改昵称）随 #172 一并移除：它们描述的是「用户自己创建
或证明身份」的输入，而本票之后不存在这条路径。
"""

from datetime import datetime

from pydantic import BaseModel, Field


class SessionSubjectResponse(BaseModel):
    """当前会话的主体。**不含邮箱、密码或任何账号字段**——那些已经不存在了。

    `subject_id` 是主体在库里的存储标识；`tenant_id` 与 `external_subject` 是它的
    两段来源。历史会话（退役期的 `account:<uuid>`）解析出来时 `tenant_id` 为空，
    `external_subject` 即整个标识——这是如实呈现，不是特例。
    """

    subject_id: str
    tenant_id: str | None = None
    external_subject: str
    display_name: str = "商城用户"

    @classmethod
    def from_storage_id(cls, storage_id: str) -> "SessionSubjectResponse":
        parts = storage_id.split(":", 2)
        if len(parts) == 3 and parts[0] == "account":
            _, tenant_id, external = parts
            return cls(subject_id=storage_id, tenant_id=tenant_id, external_subject=external)
        # `account:<uuid>`（退役期账户会话）或任何别的形状：整体当作用户标识。
        _, _, remainder = storage_id.partition(":")
        return cls(subject_id=storage_id, tenant_id=None, external_subject=remainder or storage_id)


class TicketExchangeRequest(BaseModel):
    """兑换请求：只带票据本身。**没有任何「补充身份」的字段**——身份只能来自票据。"""

    ticket: str = Field(min_length=1)


class ReportHistoryItem(BaseModel):
    id: int
    report_type: str | None = None
    department: str | None = None
    status: str
    exam_date: datetime | None = None
    created_at: datetime
    metric_count: int = 0
    finding_count: int = 0
    abnormal_count: int = 0
