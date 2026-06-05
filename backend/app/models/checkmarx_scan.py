import uuid

from sqlalchemy import Boolean, Column, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

from app.db.base import Base


class CheckmarxScanJob(Base):
    __tablename__ = "checkmarx_scan_jobs"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    created_by = Column(String, ForeignKey("users.id"), nullable=False, index=True)

    status = Column(String, default="pending", nullable=False, index=True)
    current_step = Column(String, nullable=True)
    progress = Column(Integer, default=0, nullable=False)

    project_name = Column(String, nullable=False)
    source_filename = Column(String, nullable=False)
    checkmarx_base_url = Column(String, nullable=True)
    checkmarx_project_id = Column(String, nullable=True)
    scan_id = Column(String, nullable=True, index=True)

    totals_json = Column(Text, nullable=True)
    error_message = Column(Text, nullable=True)

    started_at = Column(DateTime(timezone=True), nullable=True)
    completed_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    creator = relationship("User", foreign_keys=[created_by])
    results = relationship("CheckmarxScanResult", back_populates="job", cascade="all, delete-orphan")


class CheckmarxScanResult(Base):
    __tablename__ = "checkmarx_scan_results"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    job_id = Column(String, ForeignKey("checkmarx_scan_jobs.id", ondelete="CASCADE"), nullable=False, index=True)

    scan_id = Column(String, nullable=False, index=True)
    path_id = Column(String, nullable=False, index=True)
    vulnerability = Column(String, nullable=False)
    type = Column(String, nullable=False)
    severity = Column(Integer, nullable=True)
    url = Column(Text, nullable=False)

    ai_judgement = Column(Boolean, nullable=True)
    ai_reason = Column(Text, nullable=True)

    raw_result = Column(Text, nullable=True)
    workflow_response = Column(Text, nullable=True)

    created_at = Column(DateTime(timezone=True), server_default=func.now())

    job = relationship("CheckmarxScanJob", back_populates="results")
