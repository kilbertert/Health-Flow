"""上传策略的服务端单点与前后端契约（#171）。

`app/service/upload_policy.py` 是受理规则（扩展名与三个上限）的唯一权威。前端
不得自己记一份 —— 这里钉住三件事：

1. 端点返回的键集合**就是**那四个，多一个都是出域（服务端路径、目录、解析配置）。
2. 受理扩展名与服务端类型判定模块同源，不各写一份。
3. 端点的取值与 `app/config.py` 的设置一致 —— 运维者调大 `MAX_UPLOAD_FILES`
   之后端点立刻反映，前端不需要再发一次版本。

前端侧的落点在 `tests/test_frontend_upload_policy.py`（经 esbuild 驱动真实模块）。
两条合起来才是契约：服务端下发什么、前端按什么用。
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from app.service.report_material import ACCEPTED_EXTENSIONS
from app.service.upload_policy import upload_policy

POLICY_KEYS = {"accepted_extensions", "max_files", "max_file_bytes", "max_total_bytes"}


@pytest.fixture
def client():
    """只读端点不需要外部依赖，直接起应用即可。"""
    from app.main import app

    with patch("app.data.mysql_client.get_mysql_client"):
        yield TestClient(app)


def test_policy_publishes_only_the_four_acceptance_keys():
    """**出域面**：只有受理规则本身，没有任何服务端路径或解析配置。"""
    assert set(upload_policy()) == POLICY_KEYS


def test_accepted_extensions_come_from_the_type_authority():
    """扩展名清单不是第二份 —— 它与「什么算受理类型」同源。"""
    assert set(upload_policy()["accepted_extensions"]) == ACCEPTED_EXTENSIONS


def test_policy_follows_the_configured_limits():
    settings = SimpleNamespace(
        MAX_UPLOAD_FILES=7,
        MAX_UPLOAD_BYTES=12345,
        MAX_UPLOAD_TOTAL_BYTES=67890,
    )
    with patch("app.service.upload_policy.get_settings", return_value=settings):
        policy = upload_policy()

    assert policy["max_files"] == 7
    assert policy["max_file_bytes"] == 12345
    assert policy["max_total_bytes"] == 67890


def test_endpoint_serves_the_policy(client):
    response = client.get("/api/health/upload-policy")

    assert response.status_code == 200
    body = response.json()
    assert set(body) == POLICY_KEYS
    assert body["accepted_extensions"] == sorted(ACCEPTED_EXTENSIONS)
    # 直接给患者看的两个上限是正整数；前端据此算剩余份数与提示文案。
    assert isinstance(body["max_files"], int) and body["max_files"] > 0
    assert isinstance(body["max_file_bytes"], int) and body["max_file_bytes"] > 0


def test_endpoint_needs_no_new_role(client):
    """只读端点：不带会话也能取到（它是公开的受理规则，不是谁的资源）。"""
    assert client.get("/api/health/upload-policy").status_code == 200
