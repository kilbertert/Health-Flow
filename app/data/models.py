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
    patient_id = Column(String(128), nullable=False, index=True)
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
    # **可空，且没有默认值**：`NULL` 表示「读不出页数」（未知），与「共 1 页」
    # 是两件事。此前这一列 `NOT NULL DEFAULT 1`，于是「未知」被迫冒充 1，一个
    # 损坏的 PDF 在详情页显示成「共 1 页」（health-flow #170）。
    #
    # 没有 `default` 是**必须的**，不是省略：SQLAlchemy 的列默认值会在显式传
    # `None` 时也生效，于是「未知」又变回 1。写入方一律显式给值
    # （`app/service/report_material.py` 的 `page_count()`）。
    page_count = Column(Integer)
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
    patient_id = Column(String(128), nullable=False, index=True)
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


class HealthDataConsent(Base):
    """健康数据同意的一次表态（GLOSSARY.md 的「健康数据同意」，#269）。

    **只追加。** 这一列组没有 `updated_at`，代码里也没有任何 UPDATE 或 DELETE 的路径：
    撤回是同一条链上的另一行（`decision="revoked"`），当前状态取每项最新一条。删掉旧行会
    让「他曾经在哪一版文本下同意过」从历史里消失，而那是合规要举的证。

    `text_version` 是**表态那一刻**服务端要求的版本，不是外键也不是指针：文本版本更新后
    这一行原样留着，只是不再对当前版本有效。这正是「只追加」能同时回答两个问题的原因
    —— 当时他对哪一版表的态，以及现在还有没有效。

    `owner_id` 装的是主体的存储标识（`account:<tenant>:<sub>`），与 `medical_reports.
    owner_id` 同一形状；没有外键，理由与 `user_sessions.account_id` 相同：它是身份而不是
    一张表的键。撤回解读同意时删除该主体的报告，**不删这些行** —— 它们是那次撤回的留证。

    `deleted_report_count` **只在 `report_interpretation` 的撤回记录上**有值：报告行已经被
    删掉，那次删除的规模只能由这一行回答（#272）。它是**撤回这一刻**的事实快照，不是一份
    要维护的计数；同意（`granted`）与「商品推荐」的撤回都留空 —— 「这一项撤回不删报告」
    与「这一项撤回删了 0 份报告」是两件事，后者是一个会被读错的值。
    """

    __tablename__ = "health_data_consents"

    id = Column(Integer, primary_key=True, autoincrement=True)
    #: 主体存储标识。列宽与 `medical_reports.owner_id` 对齐（`OWNER_ID_MAX_LENGTH`）。
    owner_id = Column(String(128), nullable=False, index=True)
    #: 词表在 `app/service/consent_vocabulary.py`。
    consent_item = Column(String(64), nullable=False)
    decision = Column(String(16), nullable=False)
    #: 表态那一刻要求的文本版本（草稿编号）。它决定这条表态现在还算不算有效。
    text_version = Column(String(64), nullable=False)
    #: 这条表态从哪个通道来（`mall_h5` / `legacy_web` / `seed`）。
    source = Column(String(32), nullable=False)
    #: 这次撤回删掉了多少份报告（#272）。空 = 这一条不是一次「删报告的撤回」。
    deleted_report_count = Column(Integer)
    created_at = Column(DateTime, nullable=False, default=datetime.now)
