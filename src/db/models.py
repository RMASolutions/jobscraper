from sqlalchemy import Column, String, DateTime, Text, ForeignKey, Enum, JSON, UniqueConstraint, Uuid
from sqlalchemy.orm import relationship
from datetime import datetime
import uuid
import enum

from .connection import Base


class JobSource(str, enum.Enum):
    CONNECTING_EXPERTISE = "connecting_expertise"
    PRO_UNITY = "pro_unity"
    BNPPF = "bnppf"
    ELIA = "elia"
    AG_INSURANCE = "ag_insurance"


class Job(Base):
    """Job listing from various sources."""

    __tablename__ = "jobs"

    id = Column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    source = Column(String(50), nullable=False)  # e.g. "connecting_expertise", "ag_insurance"
    reference = Column(String(100), nullable=False)  # Job ID from source
    title = Column(String(500), nullable=False)
    client = Column(String(255), nullable=True)
    description_summary = Column(Text, nullable=True)
    location = Column(String(255), nullable=True)
    start_date = Column(String(50), nullable=True)
    end_date = Column(String(50), nullable=True)
    skills = Column(Text, nullable=True)
    url = Column(String(500), nullable=True)
    salary_band = Column(String(100), nullable=True)
    department = Column(String(255), nullable=True)
    raw_data = Column(JSON, nullable=True)  # Store all original fields

    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Unique constraint to avoid duplicates
    __table_args__ = (
        UniqueConstraint('source', 'reference', name='uq_job_source_reference'),
    )


class WorkflowStatus(str, enum.Enum):
    DRAFT = "draft"
    ACTIVE = "active"
    PAUSED = "paused"
    ARCHIVED = "archived"


class ExecutionStatus(str, enum.Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class StepStatus(str, enum.Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    SKIPPED = "skipped"


class Workflow(Base):
    """Workflow definition."""

    __tablename__ = "workflows"

    id = Column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name = Column(String(255), nullable=False)
    description = Column(Text, nullable=True)
    status = Column(Enum(WorkflowStatus), default=WorkflowStatus.DRAFT)

    # Workflow configuration as JSON (LangGraph graph definition)
    config = Column(JSON, nullable=False, default=dict)

    # Trigger configuration
    trigger_type = Column(String(50), nullable=True)  # 'schedule', 'event', 'manual'
    trigger_config = Column(JSON, nullable=True)

    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Relationships
    executions = relationship("WorkflowExecution", back_populates="workflow")


class WorkflowExecution(Base):
    """A single execution of a workflow."""

    __tablename__ = "workflow_executions"

    id = Column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    workflow_id = Column(Uuid(as_uuid=True), ForeignKey("workflows.id"), nullable=False)
    status = Column(Enum(ExecutionStatus), default=ExecutionStatus.PENDING)

    # Input/Output data
    input_data = Column(JSON, nullable=True)
    output_data = Column(JSON, nullable=True)

    # Error information
    error_message = Column(Text, nullable=True)

    # Timing
    started_at = Column(DateTime, nullable=True)
    completed_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    # Relationships
    workflow = relationship("Workflow", back_populates="executions")
    steps = relationship("WorkflowStep", back_populates="execution")


class WorkflowStep(Base):
    """Individual step within a workflow execution."""

    __tablename__ = "workflow_steps"

    id = Column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    execution_id = Column(
        Uuid(as_uuid=True), ForeignKey("workflow_executions.id"), nullable=False
    )
    step_name = Column(String(255), nullable=False)
    step_order = Column(String(50), nullable=False)  # For ordering
    status = Column(Enum(StepStatus), default=StepStatus.PENDING)

    # Step data
    input_data = Column(JSON, nullable=True)
    output_data = Column(JSON, nullable=True)
    error_message = Column(Text, nullable=True)

    # Timing
    started_at = Column(DateTime, nullable=True)
    completed_at = Column(DateTime, nullable=True)

    # Relationships
    execution = relationship("WorkflowExecution", back_populates="steps")
