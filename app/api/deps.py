"""Shared FastAPI dependencies."""

from __future__ import annotations

from types import GeneratorType

from fastapi import Depends, HTTPException, Request

from app.config import get_settings

#: 报告访问失败时随 `detail` 一起出域的**具名成因头**。
#:
#: 前端此前从「人话」里用正则捞 `401|404` 来决定说哪句话 —— 而那句人话可能被服务端的
#: `detail` 覆盖，于是同一个 401 在有的路径上被认成「登录失效」、在有的路径上被认成
#: 「商城暂时不可用」。判据必须是**结构化事实**（见 #226 的同一形状）。`detail` 保持
#: 人类可读，成因走这个头，两者各自为本。
ACCESS_REASON_HEADER = "X-Report-Access-Reason"

#: 取货为空的成因词表的家。这里只用到「报告访问」那一组；判定本身在 service 层。
from app.service.recommendation_empty_reason import REPORT_ACCESS_BY_STATUS  # noqa: E402


def db_dependency():
    """Stable FastAPI dependency wrapper that also keeps test overrides simple.

    直接在函数体内 import 使测试可以 patch ``app.data.get_db`` 生效。
    """
    from app.data import get_db as current_get_db

    value = current_get_db()
    if isinstance(value, GeneratorType):
        yield from value
    else:
        yield value


def session_owner_dependency(request: Request, db=Depends(db_dependency)) -> str | None:
    """当前请求的会话主体标识，没有有效会话时返回 None（或在要求会话时报 401）。

    **它返回的是主体的存储标识，不是账号行。** 账号体系退役之后，
    `request.state.account_id` 里装的就是主体标识（`account:<tenant>:<sub>`），
    所以这里不再有「会话 → 账号 → 主体」这条两步链。

    `REPORT_ACCOUNT_REQUIRED` 的语义随之变成「本部署是否要求先由商城票据建立会话」，
    常量名保持不变（它是既有配置，改名属于迁移）。
    """
    from app.service.sessions import session_owner_id

    owner_id = session_owner_id(request, db)
    if owner_id is None and get_settings().report_account_required:
        raise HTTPException(
            status_code=401,
            detail="请从商城入口进入后使用报告服务",
            headers={ACCESS_REASON_HEADER: REPORT_ACCESS_BY_STATUS[401]},
        )
    return owner_id
