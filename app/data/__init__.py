"""Data access layer."""

from app.data.models import (
    Base,
    MedicalReport,
    MetricRecord,
    ReportAuditEvent,
    ChatSession,
    ChatMessage,
    RoutingLog,
)
from app.data.mysql_client import (
    MySQLClient,
    get_mysql_client,
    get_db,
)

__all__ = [
    "Base",
    "MedicalReport",
    "MetricRecord",
    "ReportAuditEvent",
    "ChatSession",
    "ChatMessage",
    "RoutingLog",
    "MySQLClient",
    "get_mysql_client",
    "get_db",
]
