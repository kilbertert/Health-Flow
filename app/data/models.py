"""SQLAlchemy persistence models."""

from datetime import datetime

from sqlalchemy import (
    JSON,
    Boolean,
    Column,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import declarative_base, relationship

Base = declarative_base()


class UserAccount(Base):
    """退役期的账户行。**保留只读，至自然消亡**（health-flow #172）。

    本票之后没有任何代码路径会创建、更新或按它鉴权——自助注册、密码与登录整体退役，
    主体只能由商城票据兑换创建。这张表留着，是因为删表是数据操作而不是代码操作：
    「退役」删的是「用户自己能创建身份」的能力，不是数据。

    按邮箱反查（`email` 唯一索引）也随之失去意义：那会依赖一个我们无法验证的外部标识。
    """

    __tablename__ = "user_accounts"

    id = Column(String(36), primary_key=True)
    email = Column(String(254), nullable=False, unique=True, index=True)
    password_hash = Column(String(512), nullable=False)
    display_name = Column(String(128), nullable=False, default="健康用户")
    is_active = Column(Boolean, nullable=False, default=True)
    created_at = Column(DateTime, default=datetime.now, nullable=False)
    updated_at = Column(
        DateTime,
        default=datetime.now,
        onupdate=datetime.now,
        nullable=False,
    )



class UserSession(Base):
    """Server-side session; the browser only receives the random token.

    **`account_id` 这一列现在装的是主体的存储标识**（`account:<tenant>:<sub>`），
    不再是账号表的主键——#172 之后会话不再经过账号。列名保留（改名是迁移，不是本票），
    所以它**没有**外键：历史行指向账号，新行指向票据主体，两者不是同一张表的键。
    """

    __tablename__ = "user_sessions"

    id = Column(Integer, primary_key=True, autoincrement=True)
    account_id = Column(String(128), nullable=False)
    token_hash = Column(String(64), nullable=False, unique=True, index=True)
    created_at = Column(DateTime, default=datetime.now, nullable=False)
    expires_at = Column(DateTime, nullable=False, index=True)
    last_seen_at = Column(DateTime, default=datetime.now, nullable=False)
    revoked_at = Column(DateTime)



class MedicalReport(Base):
    __tablename__ = "medical_reports"

    id = Column(Integer, primary_key=True, autoincrement=True)
    patient_id = Column(String(64), nullable=False, index=True)
    report_type = Column(String(32))
    file_url = Column(String(512))
    parsed_content = Column(JSON)
    exam_date = Column(DateTime)
    department = Column(String(64))
    status = Column(String(32), nullable=False, default="pending_confirmation")
    subject_consistency = Column(String(16), default="same")
    evidence_result = Column(JSON)
    access_token_hash = Column(String(64))
    owner_id = Column(String(128), index=True)
    extraction_provider = Column(String(128))
    extraction_model = Column(String(128))
    extraction_prompt_version = Column(String(128))
    extraction_prompt_hash = Column(String(128))
    extraction_run_id = Column(String(128))
    provider_run_id = Column(String(256))
    provider_run_ids = Column(Text)
    evidence_correlation_id = Column(String(64))
    created_at = Column(DateTime, default=datetime.now)
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now)

    metrics = relationship("MetricRecord", back_populates="report", cascade="all, delete-orphan")
    files = relationship("ReportFile", back_populates="report", cascade="all, delete-orphan")
    audit_events = relationship("ReportAuditEvent", back_populates="report", cascade="all, delete-orphan")
    extraction_job = relationship(
        "ReportExtractionJob",
        back_populates="report",
        uselist=False,
        cascade="all, delete-orphan",
    )


class ReportFile(Base):
    __tablename__ = "report_files"

    id = Column(Integer, primary_key=True, autoincrement=True)
    report_id = Column(Integer, ForeignKey("medical_reports.id"), nullable=False, index=True)
    file_index = Column(Integer, nullable=False)
    original_filename = Column(String(255), nullable=False)
    media_type = Column(String(128), nullable=False)
    stored_path = Column(String(1024), nullable=False)
    page_count = Column(Integer, nullable=False, default=1)
    created_at = Column(DateTime, default=datetime.now)

    report = relationship("MedicalReport", back_populates="files")


class MetricRecord(Base):
    __tablename__ = "metric_records"

    id = Column(Integer, primary_key=True, autoincrement=True)
    report_id = Column(Integer, ForeignKey("medical_reports.id"), nullable=False)
    source_file_index = Column(Integer, nullable=False, default=1)
    metric_name = Column(String(128))
    metric_value = Column(String(64))
    unit = Column(String(32))
    reference_range = Column(String(64))
    trend = Column(String(16))
    abnormal_flag = Column(String(8))
    bbox = Column(JSON)
    bbox_normalized = Column(JSON)
    page_number = Column(Integer)
    evidence_text = Column(Text)
    source_id = Column(String(128))
    metric_code = Column(String(64))
    confirmation_status = Column(String(16), nullable=False, default="pending")
    confirmed_value = Column(String(64))
    confirmed_unit = Column(String(32))
    confirmed_reference_range = Column(String(64))
    confirmed_evidence_text = Column(Text)
    confirmed_at = Column(DateTime)

    report = relationship("MedicalReport", back_populates="metrics")


class ReportAuditEvent(Base):
    __tablename__ = "report_audit_events"

    id = Column(Integer, primary_key=True, autoincrement=True)
    report_id = Column(Integer, ForeignKey("medical_reports.id"), nullable=False, index=True)
    action = Column(String(64), nullable=False)
    actor = Column(String(128), nullable=False)
    correlation_id = Column(String(64))
    detail = Column(JSON)
    created_at = Column(DateTime, default=datetime.now, nullable=False)

    report = relationship("MedicalReport", back_populates="audit_events")


class ReportExtractionJob(Base):
    __tablename__ = "report_extraction_jobs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    report_id = Column(
        Integer,
        ForeignKey("medical_reports.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
        index=True,
    )
    status = Column(String(16), nullable=False, default="queued", index=True)
    attempt_count = Column(Integer, nullable=False, default=0)
    error_class = Column(String(128))
    created_at = Column(
        DateTime,
        default=datetime.now,
        server_default=func.current_timestamp(),
        nullable=False,
    )
    started_at = Column(DateTime)
    updated_at = Column(
        DateTime,
        default=datetime.now,
        onupdate=datetime.now,
        server_default=func.current_timestamp(),
        nullable=False,
    )
    completed_at = Column(DateTime)
    report = relationship("MedicalReport", back_populates="extraction_job")


class ChatSession(Base):
    __tablename__ = "chat_sessions"

    id = Column(Integer, primary_key=True, autoincrement=True)
    patient_id = Column(String(64), nullable=False, index=True)
    current_department = Column(String(64))
    agent_type = Column(String(64))
    conversation_summary = Column(Text)
    created_at = Column(DateTime, default=datetime.now)

    messages = relationship("ChatMessage", back_populates="session", cascade="all, delete-orphan")
    routing_logs = relationship("RoutingLog", back_populates="session", cascade="all, delete-orphan")


class ChatMessage(Base):
    __tablename__ = "chat_messages"

    id = Column(Integer, primary_key=True, autoincrement=True)
    session_id = Column(Integer, ForeignKey("chat_sessions.id"), nullable=False)
    role = Column(String(16))
    content = Column(Text)
    referenced_metrics = Column(JSON)
    safety_check_result = Column(String(16))
    created_at = Column(DateTime, default=datetime.now)

    session = relationship("ChatSession", back_populates="messages")


class RoutingLog(Base):
    __tablename__ = "routing_logs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    session_id = Column(Integer, ForeignKey("chat_sessions.id"), nullable=False)
    user_query = Column(Text)
    intent_distribution = Column(JSON)
    routed_department = Column(String(64))
    confidence = Column(String(32))
    created_at = Column(DateTime, default=datetime.now)

    session = relationship("ChatSession", back_populates="routing_logs")


class TicketRedemption(Base):
    """商城登录票据的一次性消费记录。

    一行 = 一张票据被兑换过一次。**唯一约束落在 `jti` 上**，这是「一次性」的实际
    载体：两条并发请求可能同时通过「查过没有」，只有数据库能裁决谁先插入。

    保留到 `purge_after`（= 票据 `exp`）即可清理——`exp` 已经封死重放窗口，
    更长的保留没有安全收益，只是多留一份凭据标识。
    """

    __tablename__ = "ticket_redemptions"
    __table_args__ = (UniqueConstraint("jti", name="uq_ticket_redemptions_jti"),)

    id = Column(Integer, primary_key=True, autoincrement=True)
    jti = Column(String(128), nullable=False, index=True)
    tenant_id = Column(String(64), nullable=False)
    subject = Column(String(128), nullable=False)
    redeemed_at = Column(DateTime, nullable=False, default=datetime.now)
    purge_after = Column(DateTime, nullable=False, index=True)


class TicketSubject(Base):
    """票据所声明的本地主体。

    身份的唯一来源是 `(tenant_id, external_subject)`——**没有密码、没有邮箱**。
    它只能由票据创建：本平台不提供注册入口，也不引入手机号等外部字段做对齐。
    """

    __tablename__ = "ticket_subjects"
    __table_args__ = (
        UniqueConstraint("tenant_id", "external_subject", name="uq_ticket_subjects_identity"),
    )

    id = Column(String(36), primary_key=True)
    tenant_id = Column(String(64), nullable=False, index=True)
    external_subject = Column(String(128), nullable=False)
    display_name = Column(String(128), nullable=False, default="商城用户")
    is_active = Column(Boolean, nullable=False, default=True)
    created_at = Column(DateTime, nullable=False, default=datetime.now)
