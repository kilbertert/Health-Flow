"""Application configuration loaded from environment variables."""

from functools import lru_cache
from typing import Optional

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    APP_ENV: str = "development"
    DATABASE_URL: Optional[str] = None

    MYSQL_HOST: str = "localhost"
    MYSQL_PORT: int = 3306
    MYSQL_USER: str = "root"
    MYSQL_PASSWORD: str = "password"
    MYSQL_DATABASE: str = "healthflow"

    MILVUS_HOST: str = "localhost"
    MILVUS_PORT: int = 19530

    NEO4J_URI: str = "bolt://localhost:7687"
    NEO4J_USER: str = "neo4j"
    NEO4J_PASSWORD: str = "password"

    VLLM_HOST: str = "localhost"
    VLLM_PORT: int = 8000
    VLLM_MODEL: str = "qwen-vl-plus"
    VLLM_API_BASE: str = ""
    VLLM_API_KEY: str = ""
    OPENAI_API_KEY: str = ""
    OPENAI_RESPONSES_URL: str = ""

    MINIMAX_API_KEY: str = ""
    MINIMAX_MODEL: str = "MiniMax-M2.7"
    EMBEDDING_MODEL: str = "BAAI/bge-large-zh-v1.5"
    EMBEDDING_OFFLINE: bool = True

    API_HOST: str = "0.0.0.0"
    API_PORT: int = 8080
    CORS_ORIGINS: str = "http://localhost:5173,http://localhost:3000"
    ROUTER_CONFIDENCE_THRESHOLD: float = 0.55
    MAX_RECURSION: int = 3
    MAX_UPLOAD_BYTES: int = 20 * 1024 * 1024
    MAX_UPLOAD_FILES: int = 20
    REPORT_PARSE_WORKERS: int = 4

    GENESIS_EVIDENCE_API_URL: str = "http://127.0.0.1:8125/api/evidence/matches"
    GENESIS_EVIDENCE_API_KEY: str = ""
    GENESIS_EVIDENCE_TIMEOUT_SECONDS: float = 30.0
    SERVE_FRONTEND: bool = False
    FRONTEND_DIST: str = "frontend/dist"

    @property
    def mysql_url(self) -> str:
        return (
            f"mysql+pymysql://{self.MYSQL_USER}:{self.MYSQL_PASSWORD}@"
            f"{self.MYSQL_HOST}:{self.MYSQL_PORT}/{self.MYSQL_DATABASE}"
        )

    @property
    def database_url(self) -> str:
        """Use SQLite for development; production can set DATABASE_URL."""
        if self.DATABASE_URL:
            return self.DATABASE_URL
        if self.APP_ENV.lower() in {"prod", "production"}:
            return self.mysql_url
        return "sqlite:///./data/healthflow.db"

    @property
    def cors_origins(self) -> list[str]:
        return [origin.strip() for origin in self.CORS_ORIGINS.split(",") if origin.strip()]

    @property
    def llm_api_base(self) -> str:
        if self.VLLM_API_BASE.strip():
            return self.VLLM_API_BASE.rstrip("/")
        if self.OPENAI_RESPONSES_URL.strip():
            return self.OPENAI_RESPONSES_URL.rstrip("/").removesuffix("/responses")
        return f"http://{self.VLLM_HOST}:{self.VLLM_PORT}/v1"

    @property
    def llm_api_key(self) -> str:
        return self.VLLM_API_KEY or self.OPENAI_API_KEY or "EMPTY"


@lru_cache
def get_settings() -> Settings:
    return Settings()
