from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from pydantic import BaseModel
from typing import Any
from uuid import UUID
from datetime import datetime
import logging

from ...db import get_db, Workflow, WorkflowExecution, ExecutionStatus
from ...workflows import workflow_registry

router = APIRouter()
logger = logging.getLogger(__name__)


class ExecutionCreate(BaseModel):
    input_data: dict[str, Any] = {}


class ExecutionResponse(BaseModel):
    id: UUID
    workflow_id: UUID
    status: ExecutionStatus
    input_data: dict[str, Any] | None
    output_data: dict[str, Any] | None
    error_message: str | None
    started_at: datetime | None
    completed_at: datetime | None
    created_at: datetime

    class Config:
        from_attributes = True


async def run_workflow_async(
    execution_id: UUID,
    workflow_type: str,
    input_data: dict[str, Any],
    db_url: str,
):
    """Background task to run a workflow."""
    from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
    from sqlalchemy.orm import sessionmaker

    # Create a new database session for background task
    engine = create_async_engine(db_url)
    async_session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async with async_session() as db:
        try:
            # Update status to running
            result = await db.execute(
                select(WorkflowExecution).where(WorkflowExecution.id == execution_id)
            )
            execution = result.scalar_one()
            execution.status = ExecutionStatus.RUNNING
            execution.started_at = datetime.utcnow()
            await db.commit()

            # Get and run workflow
            workflow = workflow_registry.create(workflow_type)
            output = await workflow.run(
                input_data=input_data,
                execution_id=str(execution_id),
            )

            # Update with results
            execution.status = ExecutionStatus.COMPLETED
            execution.output_data = output.get("output_data", {})
            execution.completed_at = datetime.utcnow()
            await db.commit()

            logger.info(f"Execution {execution_id} completed successfully")

        except Exception as e:
            logger.error(f"Execution {execution_id} failed: {e}")
            execution.status = ExecutionStatus.FAILED
            execution.error_message = str(e)
            execution.completed_at = datetime.utcnow()
            await db.commit()

        finally:
            await engine.dispose()


@router.post("/workflow/{workflow_id}/run", response_model=ExecutionResponse)
async def run_workflow(
    workflow_id: UUID,
    data: ExecutionCreate,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
):
    """Execute a workflow."""
    # Get workflow
    result = await db.execute(
        select(Workflow).where(Workflow.id == workflow_id)
    )
    workflow = result.scalar_one_or_none()

    if not workflow:
        raise HTTPException(status_code=404, detail="Workflow not found")

    workflow_type = workflow.config.get("workflow_type")
    if not workflow_type:
        raise HTTPException(
            status_code=400,
            detail="Workflow has no workflow_type in config",
        )

    # Merge workflow config with execution input
    input_data = {**workflow.config, **data.input_data}

    # Create execution record
    execution = WorkflowExecution(
        workflow_id=workflow_id,
        input_data=input_data,
        status=ExecutionStatus.PENDING,
    )
    db.add(execution)
    await db.commit()
    await db.refresh(execution)

    # Run workflow in background
    from ...core.config import settings
    background_tasks.add_task(
        run_workflow_async,
        execution.id,
        workflow_type,
        input_data,
        settings.database_url,
    )

    return execution


@router.get("/workflow/{workflow_id}", response_model=list[ExecutionResponse])
async def list_workflow_executions(
    workflow_id: UUID,
    status: ExecutionStatus | None = None,
    limit: int = 50,
    db: AsyncSession = Depends(get_db),
):
    """List executions for a workflow."""
    query = select(WorkflowExecution).where(
        WorkflowExecution.workflow_id == workflow_id
    )

    if status:
        query = query.where(WorkflowExecution.status == status)

    query = query.order_by(WorkflowExecution.created_at.desc()).limit(limit)

    result = await db.execute(query)
    return result.scalars().all()


@router.get("/{execution_id}", response_model=ExecutionResponse)
async def get_execution(
    execution_id: UUID,
    db: AsyncSession = Depends(get_db),
):
    """Get execution details."""
    result = await db.execute(
        select(WorkflowExecution).where(WorkflowExecution.id == execution_id)
    )
    execution = result.scalar_one_or_none()

    if not execution:
        raise HTTPException(status_code=404, detail="Execution not found")

    return execution


@router.post("/{execution_id}/cancel")
async def cancel_execution(
    execution_id: UUID,
    db: AsyncSession = Depends(get_db),
):
    """Cancel a running execution."""
    result = await db.execute(
        select(WorkflowExecution).where(WorkflowExecution.id == execution_id)
    )
    execution = result.scalar_one_or_none()

    if not execution:
        raise HTTPException(status_code=404, detail="Execution not found")

    if execution.status not in [ExecutionStatus.PENDING, ExecutionStatus.RUNNING]:
        raise HTTPException(
            status_code=400,
            detail=f"Cannot cancel execution with status: {execution.status}",
        )

    execution.status = ExecutionStatus.CANCELLED
    execution.completed_at = datetime.utcnow()
    await db.commit()

    return {"status": "cancelled", "id": str(execution_id)}
