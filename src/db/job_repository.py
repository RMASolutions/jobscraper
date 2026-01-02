"""
Job Repository - Database operations for Job model.
"""

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from typing import Optional
from uuid import UUID
import logging

from .models import Job, JobSource

logger = logging.getLogger(__name__)


class JobRepository:
    """Repository for Job database operations."""

    def __init__(self, session: AsyncSession):
        self.session = session

    async def save_job(self, job_data: dict, source: JobSource | str) -> tuple[Job | None, bool]:
        """
        Save a job to the database, skipping if duplicate.

        Args:
            job_data: Dictionary with job fields
            source: JobSource enum value

        Returns:
            Tuple of (Job instance or None, was_created boolean)
        """
        reference = job_data.get("reference", "")
        if not reference:
            # Generate reference from URL if not provided
            url = job_data.get("url", "")
            if url:
                # Extract ID from URL (last segment)
                reference = url.rstrip("/").split("/")[-1]
            else:
                logger.warning("Job has no reference or URL, skipping")
                return None, False

        # Convert enum to string value if needed
        source_str = source.value if isinstance(source, JobSource) else source

        # Check if job already exists
        existing = await self.get_by_source_and_reference(source_str, reference)
        if existing:
            logger.debug(f"Job already exists: {source_str}/{reference}")
            return existing, False

        # Create new job
        job = Job(
            source=source_str,
            reference=reference,
            title=job_data.get("title", "N/A"),
            client=job_data.get("client"),
            description_summary=job_data.get("description_summary"),
            location=job_data.get("location"),
            start_date=job_data.get("start_date"),
            end_date=job_data.get("end_date"),
            skills=job_data.get("skills"),
            url=job_data.get("url"),
            salary_band=job_data.get("salary_band"),
            department=job_data.get("department"),
            raw_data=job_data,
        )

        self.session.add(job)
        await self.session.commit()
        await self.session.refresh(job)

        logger.info(f"Saved new job: {job.title} ({source_str}/{reference})")
        return job, True

    async def save_jobs_batch(self, jobs_data: list[dict], source: JobSource | str) -> tuple[int, int]:
        """
        Save multiple jobs, skipping duplicates.

        Args:
            jobs_data: List of job dictionaries
            source: JobSource enum value

        Returns:
            Tuple of (new_count, skipped_count)
        """
        new_count = 0
        skipped_count = 0

        for job_data in jobs_data:
            _, was_created = await self.save_job(job_data, source)
            if was_created:
                new_count += 1
            else:
                skipped_count += 1

        return new_count, skipped_count

    async def get_by_source_and_reference(self, source: JobSource | str, reference: str) -> Optional[Job]:
        """Get a job by source and reference."""
        source_str = source.value if isinstance(source, JobSource) else source
        result = await self.session.execute(
            select(Job).where(Job.source == source_str, Job.reference == reference)
        )
        return result.scalar_one_or_none()

    async def get_by_id(self, job_id: UUID) -> Optional[Job]:
        """Get a job by ID."""
        result = await self.session.execute(
            select(Job).where(Job.id == job_id)
        )
        return result.scalar_one_or_none()

    async def get_all(
        self,
        source: Optional[JobSource | str] = None,
        limit: int = 100,
        offset: int = 0
    ) -> list[Job]:
        """Get all jobs with optional filtering."""
        query = select(Job).order_by(Job.created_at.desc())

        if source:
            source_str = source.value if isinstance(source, JobSource) else source
            query = query.where(Job.source == source_str)

        query = query.limit(limit).offset(offset)
        result = await self.session.execute(query)
        return list(result.scalars().all())

    async def count(self, source: Optional[JobSource | str] = None) -> int:
        """Count jobs with optional source filter."""
        from sqlalchemy import func
        query = select(func.count(Job.id))
        if source:
            source_str = source.value if isinstance(source, JobSource) else source
            query = query.where(Job.source == source_str)
        result = await self.session.execute(query)
        return result.scalar() or 0

    async def delete_by_id(self, job_id: UUID) -> bool:
        """Delete a job by ID."""
        job = await self.get_by_id(job_id)
        if job:
            await self.session.delete(job)
            await self.session.commit()
            return True
        return False
