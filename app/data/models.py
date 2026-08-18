"""SQLAlchemy persistence models."""

from datetime import datetime

from sqlalchemy import Column, DateTime, ForeignKey, Integer, JSON, String, Text
from sqlalchemy.orm import declarative_base, relationship


Base = declarative_base()


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
    created_at = Column(DateTime, default=datetime.now)

    metrics = relationship("MetricRecord", back_populates="report", cascade="all, delete-orphan")
    files = relationship("ReportFile", back_populates="report", cascade="all, delete-orphan")


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
    confirmed_at = Column(DateTime)

    report = relationship("MedicalReport", back_populates="metrics")


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
