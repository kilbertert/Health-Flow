"""FastAPI entry point for HealthFlow."""

import base64
import binascii
import hmac
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import PlainTextResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from app.config import get_settings
from app.data.mysql_client import get_mysql_client
from app.service.report_ownership import UNOWNED_SENTINEL, report_owner_kind


@asynccontextmanager
async def lifespan(app: FastAPI):
    database = get_mysql_client()
    try:
        database.create_tables()
        yield
    finally:
        database.close()


app = FastAPI(
    title="HealthFlow 医疗辅助系统",
    description="面向体检报告理解、证据检索和安全问答的医疗辅助系统。不能替代医生。",
    version="0.1.0",
    lifespan=lifespan,
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=get_settings().cors_origins,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["*"],
)


def _valid_basic_auth(authorization: str, username: str, password: str) -> bool:
    if not password:
        return False
    scheme, _, token = authorization.partition(" ")
    if scheme.casefold() != "basic" or not token:
        return False
    try:
        supplied = base64.b64decode(token, validate=True).decode("utf-8")
    except (binascii.Error, UnicodeDecodeError):
        return False
    supplied_user, separator, supplied_password = supplied.partition(":")
    return bool(
        separator and hmac.compare_digest(supplied_user, username) and hmac.compare_digest(supplied_password, password)
    )


@app.middleware("http")
async def basic_auth(request: Request, call_next):
    settings = get_settings()
    auth_configured = bool(settings.HEALTHFLOW_BASIC_USER.strip() and settings.HEALTHFLOW_BASIC_PASSWORD.strip())
    request.state.owner_id = UNOWNED_SENTINEL
    auth_required = bool(settings.basic_auth_enabled and auth_configured)
    if (
        request.url.path not in {"/health", "/ready"}
        and auth_required
        and not _valid_basic_auth(
            request.headers.get("authorization", ""),
            settings.HEALTHFLOW_BASIC_USER,
            settings.HEALTHFLOW_BASIC_PASSWORD,
        )
    ):
        return PlainTextResponse(
            "Unauthorized",
            status_code=401,
            headers={"WWW-Authenticate": 'Basic realm="HealthFlow", charset="UTF-8"'},
        )
    if auth_required:
        request.state.owner_id = settings.HEALTHFLOW_BASIC_USER
    return await call_next(request)


@app.get("/health")
async def health_check():
    settings = get_settings()
    return {
        "status": "healthy",
        "version": "0.1.0",
        "service": "healthflow-python",
        "database": settings.database_url.split(":", 1)[0],
    }


@app.get("/ready")
async def readiness_check():
    settings = get_settings()
    database = get_mysql_client()
    db_ok = False
    try:
        with database.engine.connect() as connection:
            connection.execute(text("SELECT 1"))
        db_ok = True
    except SQLAlchemyError:
        db_ok = False

    evidence_configured = bool(
        settings.GENESIS_EVIDENCE_API_URL.strip() and len(settings.GENESIS_EVIDENCE_API_KEY.strip()) >= 24
    )
    provider_configured = bool(
        (settings.VLLM_API_KEY.strip() or settings.OPENAI_API_KEY.strip())
        and settings.llm_api_base.strip()
        and settings.VLLM_MODEL.strip()
    )
    basic_auth_configured = bool(
        settings.basic_auth_enabled
        and settings.HEALTHFLOW_BASIC_USER.strip()
        and settings.HEALTHFLOW_BASIC_PASSWORD.strip()
    )
    report_owner = report_owner_kind(
        account_required=settings.report_account_required,
        operator_configured=basic_auth_configured,
    )
    # 商城配置缺失在**开发环境合法**（推荐区块安静地不出现），但在生产是配置事故：
    # 一个上线了的部署不该把"货架永远是空的"当作正常。所以按环境区分，而不是一律
    # degraded——否则一个刻意不接商城的开发部署会永远报 degraded，真故障被淹没。
    #
    # 这里**不做商城连通性探测**。readiness 是廉价、本地的信号，把第三方拉进来会让
    # 商城的一次抖动把本服务摘出负载均衡——而它只影响一个装饰性区块，报告解读不受影响。
    # 「配置了但商城当前不可达」由 recommendation 端点如实返回的 `mall_unavailable`
    # 与日志表达，那是按需观测，不是每次探针都去打一遍别人的服务。
    configured = settings.mall_webapi_configured
    production = settings.APP_ENV.casefold() in {"prod", "production"}
    mall_status = "configured" if configured else ("unconfigured" if not production else "missing")

    core_ready = db_ok and evidence_configured and provider_configured
    return {
        "status": "degraded" if (not core_ready or mall_status == "missing") else "ready",
        "database": "ok" if db_ok else "unavailable",
        "evidence_service": "configured" if evidence_configured else "unconfigured",
        "mall_goods": mall_status,
        "report_provider": "configured" if provider_configured else "unconfigured",
        "report_owner": report_owner,
        "account_auth": "required" if settings.report_account_required else "optional",
        "report_model": settings.VLLM_MODEL if provider_configured else "unconfigured",
    }


from app.api import auth, report

app.include_router(auth.router, prefix="/api/auth", tags=["Account"])
app.include_router(report.router, prefix="/api/health", tags=["Report"])

settings = get_settings()
if settings.SERVE_FRONTEND:
    frontend_dist = Path(settings.FRONTEND_DIST).expanduser().resolve()
    if not (frontend_dist / "index.html").is_file():
        raise RuntimeError(f"frontend build not found: {frontend_dist}")
    app.mount("/", StaticFiles(directory=frontend_dist, html=True), name="frontend")
else:

    @app.get("/")
    async def root():
        return {
            "name": "HealthFlow Medical Assistant",
            "message": "HealthFlow Medical Assistant API",
            "version": "0.1.0",
            "scope": "medical-assistance-only",
            "docs": "/docs",
        }
