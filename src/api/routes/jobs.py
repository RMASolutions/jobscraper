"""
Jobs API - Query and manage scraped jobs from the database.
"""

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession
from pydantic import BaseModel
from typing import Optional
from uuid import UUID
from datetime import datetime

from ...db import get_db, Job, JobSource, JobRepository

router = APIRouter()


class JobResponse(BaseModel):
    """Response model for a job."""
    id: UUID
    source: JobSource
    reference: str
    title: str
    client: Optional[str] = None
    description_summary: Optional[str] = None
    location: Optional[str] = None
    start_date: Optional[str] = None
    end_date: Optional[str] = None
    skills: Optional[str] = None
    url: Optional[str] = None
    salary_band: Optional[str] = None
    department: Optional[str] = None
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


class JobListResponse(BaseModel):
    """Response model for job list with pagination info."""
    jobs: list[JobResponse]
    total: int
    limit: int
    offset: int


class JobStats(BaseModel):
    """Response model for job statistics."""
    total: int
    by_source: dict[str, int]


@router.get("/", response_model=JobListResponse)
async def list_jobs(
    source: Optional[JobSource] = None,
    limit: int = Query(default=50, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    db: AsyncSession = Depends(get_db),
):
    """
    List jobs with optional filtering by source.

    - **source**: Filter by job source (connecting_expertise, pro_unity, bnppf, elia)
    - **limit**: Maximum number of jobs to return (default: 50, max: 500)
    - **offset**: Number of jobs to skip for pagination (default: 0)
    """
    repo = JobRepository(db)

    jobs = await repo.get_all(source=source, limit=limit, offset=offset)
    total = await repo.count(source=source)

    return JobListResponse(
        jobs=[JobResponse.model_validate(job) for job in jobs],
        total=total,
        limit=limit,
        offset=offset
    )


@router.get("/stats", response_model=JobStats)
async def get_job_stats(
    db: AsyncSession = Depends(get_db),
):
    """Get job statistics including count by source."""
    repo = JobRepository(db)

    total = await repo.count()

    by_source = {}
    for source in JobSource:
        count = await repo.count(source=source)
        by_source[source.value] = count

    return JobStats(total=total, by_source=by_source)


@router.get("/sources")
async def list_sources():
    """List available job sources."""
    return {
        "sources": [
            {"value": source.value, "name": source.name}
            for source in JobSource
        ]
    }


@router.get("/{job_id}", response_model=JobResponse)
async def get_job(
    job_id: UUID,
    db: AsyncSession = Depends(get_db),
):
    """Get a specific job by ID."""
    repo = JobRepository(db)
    job = await repo.get_by_id(job_id)

    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    return JobResponse.model_validate(job)


@router.delete("/{job_id}")
async def delete_job(
    job_id: UUID,
    db: AsyncSession = Depends(get_db),
):
    """Delete a job by ID."""
    repo = JobRepository(db)
    deleted = await repo.delete_by_id(job_id)

    if not deleted:
        raise HTTPException(status_code=404, detail="Job not found")

    return {"status": "deleted", "id": str(job_id)}


@router.get("/source/{source}", response_model=JobListResponse)
async def list_jobs_by_source(
    source: JobSource,
    limit: int = Query(default=50, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    db: AsyncSession = Depends(get_db),
):
    """
    List jobs for a specific source.

    - **source**: Job source (connecting_expertise, pro_unity, bnppf, elia)
    - **limit**: Maximum number of jobs to return (default: 50, max: 500)
    - **offset**: Number of jobs to skip for pagination (default: 0)
    """
    repo = JobRepository(db)

    jobs = await repo.get_all(source=source, limit=limit, offset=offset)
    total = await repo.count(source=source)

    return JobListResponse(
        jobs=[JobResponse.model_validate(job) for job in jobs],
        total=total,
        limit=limit,
        offset=offset
    )
