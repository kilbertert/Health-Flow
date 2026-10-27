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


class TicketExchangeResponse(SessionSubjectResponse):
    """兑换的响应体：主体，**外加会话令牌与过期时刻**。

    票据兑换过去只写 Cookie —— 同源的旧前端不需要读令牌。商城 H5 在另一个源上，
    浏览器不会替它带上本仓的 Cookie（ADR 0026），所以令牌必须**经响应体**交给它，
    由模块自己放进 `Authorization: Bearer` 并存进 `sessionStorage`。

    令牌与 Cookie 指向**同一条**服务端会话行：`session_expires_at` 就是那一行的过期时刻，
    不是另算的第二份寿命。吊销（退出登录）对两者同时生效。
    """

    session_token: str
    session_expires_at: datetime


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
    # 需要患者处理的行数（GLOSSARY.md 的「报告指标计数」）：异常的行，加上「判定不出来、
    # 但模型宣称异常」的行。它是**另一个谓词**，不是 `abnormal_count` 的第二种读法 ——
    # 两者在某份报告上可以不同，那是两个具名事实的差，不是一个数的两套算法。
    needs_review_count: int = 0
    # 判定在参考范围内的行数（`N`，且患者没有排除）。
    #
    # **名字与同块的 `*_count` 不同，是刻意的**（#270）：报告详情的计数块叫这个字段
    # `in_range`，而患者侧要在两个契约之间**对同一个数**，两处同名就不必在模块里翻译一次
    # 键名 —— 翻译点就是下一次漂移点。这个名字由票面写定，不是随手省掉后缀。
    in_range: int = 0
