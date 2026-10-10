"""Tests for FastAPI main application."""

import pytest
from fastapi.testclient import TestClient

from app.config import get_settings
from app.main import _valid_basic_auth, app


@pytest.fixture
def client():
    """Create test client."""
    return TestClient(app)


def test_health_check(client):
    """Test health check endpoint."""
    response = client.get("/health")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "healthy"
    assert "version" in data
    assert data["version"] == "0.1.0"


def test_root(client):
    """Test root endpoint."""
    response = client.get("/")
    assert response.status_code == 200
    data = response.json()
    assert "HealthFlow" in data["message"]
    assert data["version"] == "0.1.0"
    assert data["docs"] == "/docs"


def test_basic_auth_validation():
    import base64

    valid = base64.b64encode(b"reviewer:secret").decode()
    assert _valid_basic_auth(f"Basic {valid}", "reviewer", "secret") is True
    assert _valid_basic_auth(f"Basic {valid}", "reviewer", "wrong") is False
    assert _valid_basic_auth("Bearer token", "reviewer", "secret") is False
    assert _valid_basic_auth("", "reviewer", "") is False


def test_basic_auth_middleware_challenges_and_accepts(client):
    settings = get_settings()
    previous_user = settings.HEALTHFLOW_BASIC_USER
    previous_password = settings.HEALTHFLOW_BASIC_PASSWORD
    previous_enabled = settings.HEALTHFLOW_BASIC_AUTH_ENABLED
    settings.HEALTHFLOW_BASIC_USER = "reviewer"
    settings.HEALTHFLOW_BASIC_PASSWORD = "secret"
    settings.HEALTHFLOW_BASIC_AUTH_ENABLED = True
    try:
        denied = client.get("/")
        assert denied.status_code == 401
        assert denied.headers["www-authenticate"].startswith("Basic ")
        assert client.get("/", auth=("reviewer", "secret")).status_code == 200
        assert client.get("/health").status_code == 200
    finally:
        settings.HEALTHFLOW_BASIC_USER = previous_user
        settings.HEALTHFLOW_BASIC_PASSWORD = previous_password
        settings.HEALTHFLOW_BASIC_AUTH_ENABLED = previous_enabled


def test_ready_requires_a_full_evidence_api_key(client):
    settings = get_settings()
    previous_url = settings.GENESIS_EVIDENCE_API_URL
    previous_key = settings.GENESIS_EVIDENCE_API_KEY
    previous_vllm_key = settings.VLLM_API_KEY
    previous_openai_key = settings.OPENAI_API_KEY
    previous_model = settings.VLLM_MODEL
    settings.GENESIS_EVIDENCE_API_URL = "http://127.0.0.1:8125/api/evidence/matches"
    settings.VLLM_API_KEY = ""
    settings.OPENAI_API_KEY = ""
    settings.VLLM_MODEL = "gpt-5.6-sol"
    try:
        settings.GENESIS_EVIDENCE_API_KEY = "short"
        degraded = client.get("/ready")
        assert degraded.status_code == 200
        assert degraded.json()["status"] == "degraded"
        assert degraded.json()["evidence_service"] == "unconfigured"
        assert degraded.json()["report_provider"] == "unconfigured"

        settings.GENESIS_EVIDENCE_API_KEY = "a" * 24
        still_degraded = client.get("/ready")
        assert still_degraded.status_code == 200
        assert still_degraded.json()["status"] == "degraded"
        assert still_degraded.json()["evidence_service"] == "configured"

        settings.OPENAI_API_KEY = "provider-key"
        ready = client.get("/ready")
        assert ready.status_code == 200
        assert ready.json()["status"] == "ready"
        assert ready.json()["report_provider"] == "configured"
        assert ready.json()["report_owner"] == "unconfigured"
    finally:
        settings.GENESIS_EVIDENCE_API_URL = previous_url
        settings.GENESIS_EVIDENCE_API_KEY = previous_key
        settings.VLLM_API_KEY = previous_vllm_key
        settings.OPENAI_API_KEY = previous_openai_key
        settings.VLLM_MODEL = previous_model


def test_ready_is_unknown_when_the_env_file_is_not_declared(client):
    """没有 `HEALTHFLOW_ENV_FILE` 时如实回答 unknown，而不是假装 current。

    这条同时也是**部署顺序**的守卫：unit 的安装是合并之后的操作者步骤，而合并就会触发
    部署。unknown **不降级**，所以那次部署不会被自己的自检打回滚；而 stale 检测不依赖
    `.path` 是否装好（文件一 > 进程启动时刻就会被看出来），所以 unknown 不需要当警报。
    """
    settings = get_settings()
    previous = settings.HEALTHFLOW_ENV_FILE
    settings.HEALTHFLOW_ENV_FILE = ""
    try:
        body = client.get("/ready").json()
        assert body["config_freshness"] == "unknown"
        assert body["config_file_changed_at"] is None
        assert body["process_started_at"] is not None, "进程启动时刻应当总能读到（Linux）"
    finally:
        settings.HEALTHFLOW_ENV_FILE = previous


def test_ready_is_current_when_the_env_file_predates_the_process(client, tmp_path):
    """文件比进程旧 = 本进程加载的就是磁盘上这一份。用真实临时文件驱动，不 mock stat。

    必须把 mtime 显式挪到过去：测试进程先于这个文件存在，刚写出来的文件在判定上就是
    「比进程新」，会（正确地）报 stale。真实情形里这就是「文件先写好、进程后启动」，
    也正是部署的样子 —— 部署先落 env，再重启服务。
    """
    import os
    import time

    env_file = tmp_path / "health-flow.env"
    env_file.write_text("VLLM_MODEL=test\n", encoding="utf-8")
    past = time.time() - 3600
    os.utime(env_file, (past, past))

    settings = get_settings()
    previous = settings.HEALTHFLOW_ENV_FILE
    settings.HEALTHFLOW_ENV_FILE = str(env_file)
    try:
        body = client.get("/ready").json()
        assert body["config_freshness"] == "current"
        assert body["config_file_changed_at"] is not None
    finally:
        settings.HEALTHFLOW_ENV_FILE = previous


def test_ready_is_stale_and_degraded_when_the_env_file_is_newer_than_the_process(client, tmp_path):
    """**本缺陷的核心断言**：文件在本进程启动之后被替换过 -> stale，且 status 降为 degraded。

    2026-10-10 的事故就是这一形状：worker 起于 09-30 16:49，env 文件在 10-10 14:08:56 被
    替换，于是它对 provider 报 401，而 `/ready` 一直报 configured（#201）。

    这里把文件的 mtime 显式推到未来，而不是「刚写过」——因为 `CLOCK_SKEW_SECONDS` 的余量
    会吃掉「启动后同一秒内被碰一下」这种情形，用未来时刻才能稳定地、明确地跨越启动时刻。
    降为 degraded 之后，`deploy-36.sh` 的自检（断言 status == "ready"）会自己抓到它。
    """
    import os
    import time

    env_file = tmp_path / "health-flow.env"
    env_file.write_text("VLLM_MODEL=test\n", encoding="utf-8")
    future = time.time() + 120
    os.utime(env_file, (future, future))

    settings = get_settings()
    previous = settings.HEALTHFLOW_ENV_FILE
    settings.HEALTHFLOW_ENV_FILE = str(env_file)
    try:
        body = client.get("/ready").json()
        assert body["config_freshness"] == "stale", "文件比进程新，必须判为过期"
        assert body["status"] == "degraded", "过期必须降级，否则部署自检不会抓到它"
    finally:
        settings.HEALTHFLOW_ENV_FILE = previous


def test_ready_is_unknown_when_the_declared_env_file_cannot_be_read(client, tmp_path):
    """声明了路径却读不到：报 unknown，不是 current（那会是一句没有依据的断言）。

    这是「配置错误」而不是「过期」——我们确实不知道磁盘上有什么。
    """
    settings = get_settings()
    previous = settings.HEALTHFLOW_ENV_FILE
    settings.HEALTHFLOW_ENV_FILE = str(tmp_path / "does-not-exist.env")
    try:
        body = client.get("/ready").json()
        assert body["config_freshness"] == "unknown"
        assert body["config_file_changed_at"] is None
    finally:
        settings.HEALTHFLOW_ENV_FILE = previous


def test_production_uses_account_auth_without_basic_challenge(client):
    settings = get_settings()
    previous_env = settings.APP_ENV
    previous_user = settings.HEALTHFLOW_BASIC_USER
    previous_password = settings.HEALTHFLOW_BASIC_PASSWORD
    previous_enabled = settings.HEALTHFLOW_BASIC_AUTH_ENABLED
    settings.APP_ENV = "production"
    settings.HEALTHFLOW_BASIC_USER = "healthflow"
    settings.HEALTHFLOW_BASIC_PASSWORD = ""
    settings.HEALTHFLOW_BASIC_AUTH_ENABLED = None
    try:
        assert client.get("/").status_code == 200
        assert client.get("/ready").json()["report_owner"] == "account"
        assert client.get("/ready").json()["status"] == "degraded"
    finally:
        settings.APP_ENV = previous_env
        settings.HEALTHFLOW_BASIC_USER = previous_user
        settings.HEALTHFLOW_BASIC_PASSWORD = previous_password
        settings.HEALTHFLOW_BASIC_AUTH_ENABLED = previous_enabled
