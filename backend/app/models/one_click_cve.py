import uuid

from sqlalchemy import Boolean, Column, DateTime, Float, ForeignKey, Integer, JSON, String, Text
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

from app.db.base import Base


class OneClickCveBatchStatus:
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    EXHAUSTED = "exhausted"


class OneClickCveProjectStatus:
    CANDIDATE = "candidate"
    IMPORTING = "importing"
    AUDITING = "auditing"
    COMPLETED = "completed"
    SKIPPED = "skipped"
    FAILED = "failed"


class OneClickCveBatch(Base):
    __tablename__ = "one_click_cve_batches"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    user_id = Column(String(36), ForeignKey("users.id"), nullable=False, index=True)

    requested_count = Column(Integer, nullable=False)
    found_count = Column(Integer, default=0, nullable=False)
    status = Column(String(30), default=OneClickCveBatchStatus.PENDING, nullable=False, index=True)
    current_step = Column(String(255), nullable=True)
    error_message = Column(Text, nullable=True)
    summary_json = Column(JSON, nullable=True)

    started_at = Column(DateTime(timezone=True), nullable=True)
    completed_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), index=True)
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())

    user = relationship("User", foreign_keys=[user_id])
    projects = relationship(
        "OneClickCveBatchProject",
        back_populates="batch",
        cascade="all, delete-orphan",
        order_by="OneClickCveBatchProject.created_at",
    )


class OneClickCveBatchProject(Base):
    __tablename__ = "one_click_cve_batch_projects"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    batch_id = Column(
        String(36),
        ForeignKey("one_click_cve_batches.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    project_id = Column(String(36), ForeignKey("projects.id", ondelete="SET NULL"), nullable=True, index=True)
    agent_task_id = Column(String(36), ForeignKey("agent_tasks.id", ondelete="SET NULL"), nullable=True, index=True)

    github_full_name = Column(String(255), nullable=False, index=True)
    repository_url = Column(Text, nullable=False)
    description = Column(Text, nullable=True)
    language = Column(String(80), nullable=True)
    stars = Column(Integer, default=0, nullable=False)
    pushed_at = Column(DateTime(timezone=True), nullable=True)
    updated_at = Column(DateTime(timezone=True), nullable=True)
    default_branch = Column(String(255), nullable=True)
    version_label = Column(String(255), nullable=True, index=True)
    version_source = Column(String(50), nullable=True)
    has_security_advisory = Column(Boolean, default=False, nullable=False)
    advisory_count = Column(Integer, default=0, nullable=False)
    has_security_policy = Column(Boolean, default=False, nullable=False)
    has_private_vulnerability_reporting = Column(Boolean, default=False, nullable=False)
    score = Column(Float, default=0.0, nullable=False)

    status = Column(String(30), default=OneClickCveProjectStatus.CANDIDATE, nullable=False, index=True)
    findings_count = Column(Integer, default=0, nullable=False)
    error_message = Column(Text, nullable=True)
    metadata_json = Column(JSON, nullable=True)

    created_at = Column(DateTime(timezone=True), server_default=func.now(), index=True)
    updated_at_local = Column(DateTime(timezone=True), onupdate=func.now())

    batch = relationship("OneClickCveBatch", back_populates="projects")
    project = relationship("Project", foreign_keys=[project_id])
    agent_task = relationship("AgentTask", foreign_keys=[agent_task_id])
