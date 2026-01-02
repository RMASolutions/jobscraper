from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from pydantic import BaseModel
from typing import Any
from uuid import UUID

from ...db import get_db, Workflow, WorkflowStatus
from ...workflows import workflow_registry

router = APIRouter()


class WorkflowCreate(BaseModel):
    name: str
    description: str | None = None
    workflow_type: str  # Maps to registered workflow
    config: dict[str, Any] = {}
    trigger_type: str | None = None
    trigger_config: dict[str, Any] | None = None


class WorkflowUpdate(BaseModel):
    name: str | None = None
    description: str | None = None
    config: dict[str, Any] | None = None
    status: WorkflowStatus | None = None
    trigger_type: str | None = None
    trigger_config: dict[str, Any] | None = None


class WorkflowResponse(BaseModel):
    id: UUID
    name: str
    description: str | None
    status: WorkflowStatus
    config: dict[str, Any]
    trigger_type: str | None
    trigger_config: dict[str, Any] | None

    class Config:
        from_attributes = True


@router.get("/types")
async def list_workflow_types():
    """List available workflow types."""
    return {"workflow_types": workflow_registry.list()}


@router.post("/", response_model=WorkflowResponse)
async def create_workflow(
    data: WorkflowCreate,
    db: AsyncSession = Depends(get_db),
):
    """Create a new workflow."""
    # Verify workflow type exists
    if data.workflow_type not in workflow_registry.list():
        raise HTTPException(
            status_code=400,
            detail=f"Unknown workflow type: {data.workflow_type}. "
            f"Available types: {workflow_registry.list()}",
        )

    workflow = Workflow(
        name=data.name,
        description=data.description,
        config={"workflow_type": data.workflow_type, **data.config},
        trigger_type=data.trigger_type,
        trigger_config=data.trigger_config,
    )

    db.add(workflow)
    await db.commit()
    await db.refresh(workflow)

    return workflow


@router.get("/", response_model=list[WorkflowResponse])
async def list_workflows(
    status: WorkflowStatus | None = None,
    db: AsyncSession = Depends(get_db),
):
    """List all workflows."""
    query = select(Workflow)
    if status:
        query = query.where(Workflow.status == status)

    result = await db.execute(query)
    return result.scalars().all()


@router.get("/{workflow_id}", response_model=WorkflowResponse)
async def get_workflow(
    workflow_id: UUID,
    db: AsyncSession = Depends(get_db),
):
    """Get a specific workflow."""
    result = await db.execute(
        select(Workflow).where(Workflow.id == workflow_id)
    )
    workflow = result.scalar_one_or_none()

    if not workflow:
        raise HTTPException(status_code=404, detail="Workflow not found")

    return workflow


@router.patch("/{workflow_id}", response_model=WorkflowResponse)
async def update_workflow(
    workflow_id: UUID,
    data: WorkflowUpdate,
    db: AsyncSession = Depends(get_db),
):
    """Update a workflow."""
    result = await db.execute(
        select(Workflow).where(Workflow.id == workflow_id)
    )
    workflow = result.scalar_one_or_none()

    if not workflow:
        raise HTTPException(status_code=404, detail="Workflow not found")

    update_data = data.model_dump(exclude_unset=True)
    for field, value in update_data.items():
        setattr(workflow, field, value)

    await db.commit()
    await db.refresh(workflow)

    return workflow


@router.delete("/{workflow_id}")
async def delete_workflow(
    workflow_id: UUID,
    db: AsyncSession = Depends(get_db),
):
    """Delete a workflow."""
    result = await db.execute(
        select(Workflow).where(Workflow.id == workflow_id)
    )
    workflow = result.scalar_one_or_none()

    if not workflow:
        raise HTTPException(status_code=404, detail="Workflow not found")

    await db.delete(workflow)
    await db.commit()

    return {"status": "deleted", "id": str(workflow_id)}
