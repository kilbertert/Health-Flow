"""Shared FastAPI dependencies."""

from __future__ import annotations

from types import GeneratorType

from fastapi import Depends, HTTPException, Request

from app.config import get_settings
from app.data.models import UserAccount


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


def report_account_dependency(request: Request, db=Depends(db_dependency)) -> UserAccount | None:
    from app.service.auth import account_for_request

    account = account_for_request(request, db)
    if account is None and get_settings().report_account_required:
        raise HTTPException(status_code=401, detail="请先登录后使用报告服务")
    return account
