"""Data access layer."""

from app.data.models import (
    Base,
    ChatMessage,
    ChatSession,
    MedicalReport,
    MetricRecord,
    ReportAuditEvent,
    ReportExtractionJob,
    RoutingLog,
)
from app.data.mysql_client import (
    MySQLClient,
    get_db,
    get_mysql_client,
)

__all__ = [
    "Base",
    "ChatMessage",
    "ChatSession",
    "MedicalReport",
    "MetricRecord",
    "MySQLClient",
    "ReportAuditEvent",
    "ReportExtractionJob",
    "RoutingLog",
    "get_db",
    "get_mysql_client",
]
