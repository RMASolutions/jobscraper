"""
Elia/TAPFIN Job Email Workflow

Reads job opportunity emails from tapfin.support@tapfin.be,
extracts job details, and outputs to CSV.
"""

from typing import Any
import logging
import csv
import re
from pathlib import Path
from datetime import datetime, timedelta

from ..base import BaseWorkflow, WorkflowState
from ..registry import register_workflow
from ...providers import get_llm_provider
from ...integrations.m365 import M365EmailClient
from ...core.config import settings
from ...db import AsyncSessionLocal, JobRepository, JobSource

logger = logging.getLogger(__name__)


@register_workflow("elia_jobs")
class EliaJobsWorkflow(BaseWorkflow):
    """
    Workflow to extract job listings from Elia/TAPFIN emails.

    Steps:
    1. Read emails from tapfin.support@tapfin.be (today or specified date range)
    2. Parse job details from email content
    3. Save to database (skip duplicates)
    4. Output to CSV
    """

    # Email configuration
    SENDER_EMAIL = "tapfin.support@tapfin.be"
    SUBJECT_PATTERN = r"TAPFIN for Elia has launched a new request"

    def __init__(self):
        super().__init__("elia_jobs")
        self.llm = get_llm_provider()

    def get_entry_point(self) -> str:
        return "fetch_emails"

    def define_nodes(self) -> dict[str, callable]:
        return {
            "fetch_emails": self.fetch_emails_step,
            "parse_jobs": self.parse_jobs_step,
            "save_to_db": self.save_to_db_step,
            "generate_output": self.generate_output_step,
            "handle_error": self.handle_error_step,
        }

    def define_edges(self) -> list[tuple]:
        return [
            ("fetch_emails", self._has_emails, {"yes": "parse_jobs", "no": "generate_output"}),
            ("parse_jobs", "save_to_db"),
            ("save_to_db", "generate_output"),
            ("generate_output", "END"),
            ("handle_error", "END"),
        ]

    # Conditions
    def _has_emails(self, state: WorkflowState) -> str:
        return "yes" if state["data"].get("raw_emails") else "no"

    # Steps
    async def fetch_emails_step(self, state: WorkflowState) -> dict:
        """Fetch job emails from M365 mailbox."""
        logger.info("Executing fetch_emails step")

        input_data = state["input_data"]
        days_back = input_data.get("days_back", 1)

        # Initialize M365 client
        m365_client = M365EmailClient(
            tenant_id=settings.m365_tenant_id,
            client_id=settings.m365_client_id,
            client_secret=settings.m365_client_secret,
            user_email=settings.m365_user_email
        )

        try:
            token = await m365_client._get_token()
            from_date = datetime.utcnow() - timedelta(days=days_back)

            import httpx
            url = f"{m365_client.GRAPH_URL}/users/{m365_client.user_email}/messages"
            params = {
                "$filter": f"receivedDateTime ge {from_date.isoformat()}Z and from/emailAddress/address eq '{self.SENDER_EMAIL}'",
                "$orderby": "receivedDateTime desc",
                "$top": 50,
                "$select": "subject,body,from,receivedDateTime"
            }

            async with httpx.AsyncClient() as client:
                response = await client.get(
                    url,
                    params=params,
                    headers={"Authorization": f"Bearer {token}"}
                )
                response.raise_for_status()
                data = response.json()

            emails = data.get("value", [])

            # Filter for job emails
            job_emails = [
                email for email in emails
                if re.search(self.SUBJECT_PATTERN, email.get("subject", ""), re.IGNORECASE)
            ]

            logger.info(f"Found {len(job_emails)} job emails from Elia/TAPFIN")

            return {
                "data": {**state.get("data", {}), "raw_emails": job_emails},
                "messages": [f"Fetched {len(job_emails)} job emails"],
                "current_step": "fetch_emails",
            }

        except Exception as e:
            logger.error(f"Failed to fetch emails: {e}")
            return {
                "data": {**state.get("data", {}), "raw_emails": []},
                "error": str(e),
                "messages": [f"Failed to fetch emails: {e}"],
            }

    async def parse_jobs_step(self, state: WorkflowState) -> dict:
        """Parse job details from email content."""
        logger.info("Executing parse_jobs step")

        raw_emails = state["data"].get("raw_emails", [])
        jobs = []

        for email in raw_emails:
            try:
                subject = email.get("subject", "")
                body_content = email.get("body", {}).get("content", "")
                body_type = email.get("body", {}).get("contentType", "text")
                received_date = email.get("receivedDateTime", "")

                # Convert HTML to text if needed
                if body_type.lower() == "html":
                    from bs4 import BeautifulSoup
                    soup = BeautifulSoup(body_content, "html.parser")
                    body_text = soup.get_text(separator="\n")
                else:
                    body_text = body_content

                # Extract job reference from subject or body
                ref_match = re.search(r'(SRQ\d+)', subject) or re.search(r'(SRQ\d+)', body_text)
                job_reference = ref_match.group(1) if ref_match else "N/A"

                # Extract service/title from subject
                title_match = re.search(r'request for service:\s*(.+?)\s*\(SRQ', subject, re.IGNORECASE)
                job_title = title_match.group(1).strip() if title_match else self._extract_field(body_text, "Service")

                # Extract link
                link_match = re.search(r'(https://tapfin[^\s<>"]+)', body_text)
                job_link = link_match.group(1) if link_match else "N/A"

                # Parse structured fields from body
                job_data = {
                    "reference": job_reference,
                    "title": job_title or "N/A",
                    "department": self._extract_field(body_text, "Department"),
                    "salary_band": self._extract_field(body_text, "Salary Band"),
                    "segment": self._extract_field(body_text, "Segment"),
                    "start_date": self._extract_field(body_text, "Start Date"),
                    "end_date": self._extract_field(body_text, "End Date"),
                    "deadline": self._extract_field(body_text, "Deadline for Proposals"),
                    "msp_owner": self._extract_field(body_text, "MSP Owner"),
                    "link": job_link,
                    "received_date": received_date[:10] if received_date else "N/A",
                }

                jobs.append(job_data)
                logger.info(f"Parsed job: {job_title} ({job_reference})")

            except Exception as e:
                logger.warning(f"Failed to parse email: {e}")

        return {
            "data": {**state["data"], "parsed_jobs": jobs},
            "messages": [f"Parsed {len(jobs)} jobs from emails"],
            "current_step": "parse_jobs",
        }

    async def save_to_db_step(self, state: WorkflowState) -> dict:
        """Save jobs to database, skipping duplicates."""
        logger.info("Executing save_to_db step")

        parsed_jobs = state["data"].get("parsed_jobs", [])

        if not parsed_jobs:
            return {
                "data": {**state["data"], "new_jobs": 0, "skipped_jobs": 0},
                "messages": ["No jobs to save to database"],
                "current_step": "save_to_db"
            }

        async with AsyncSessionLocal() as session:
            repo = JobRepository(session)
            new_count, skipped_count = await repo.save_jobs_batch(
                parsed_jobs, JobSource.ELIA
            )

        logger.info(f"Saved {new_count} new jobs, skipped {skipped_count} duplicates")

        return {
            "data": {**state["data"], "new_jobs": new_count, "skipped_jobs": skipped_count},
            "messages": [f"Saved {new_count} new jobs to DB, skipped {skipped_count} duplicates"],
            "current_step": "save_to_db"
        }

    def _extract_field(self, text: str, field_name: str) -> str:
        """Extract a field value from text."""
        pattern = rf"{re.escape(field_name)}[:\s]+(.+?)(?:\n|$)"
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            value = match.group(1).strip()
            value = re.sub(r'\s+', ' ', value)
            return value[:200] if value else "N/A"
        return "N/A"

    async def generate_output_step(self, state: WorkflowState) -> dict:
        """Generate CSV file with job listings."""
        logger.info("Executing generate_output step")

        jobs = state["data"].get("parsed_jobs", [])
        output_dir = state["input_data"].get("output_dir", ".")

        if not jobs:
            return {
                "output_data": {"summary": "No job emails found.", "csv_file": None, "count": 0},
                "messages": ["No jobs to output."],
                "current_step": "generate_output"
            }

        # Generate CSV file
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        csv_filename = f"jobs_elia_{timestamp}.csv"
        csv_path = Path(output_dir) / csv_filename

        csv_headers = [
            "Reference", "Title", "Department", "Salary Band", "Segment",
            "Start Date", "End Date", "Deadline", "MSP Owner", "Link", "Received Date"
        ]

        with open(csv_path, "w", newline="", encoding="utf-8") as csvfile:
            writer = csv.writer(csvfile)
            writer.writerow(csv_headers)

            for job in jobs:
                writer.writerow([
                    job.get("reference", "N/A"),
                    job.get("title", "N/A"),
                    job.get("department", "N/A"),
                    job.get("salary_band", "N/A"),
                    job.get("segment", "N/A"),
                    job.get("start_date", "N/A"),
                    job.get("end_date", "N/A"),
                    job.get("deadline", "N/A"),
                    job.get("msp_owner", "N/A"),
                    job.get("link", "N/A"),
                    job.get("received_date", "N/A"),
                ])

        logger.info(f"CSV file created: {csv_path}")

        # Create text summary
        new_jobs = state["data"].get("new_jobs", 0)
        skipped_jobs = state["data"].get("skipped_jobs", 0)

        summary_text = f"Found {len(jobs)} Elia/TAPFIN job(s):\n"
        summary_text += f"  - New jobs saved to DB: {new_jobs}\n"
        summary_text += f"  - Duplicates skipped: {skipped_jobs}\n\n"
        for i, job in enumerate(jobs, 1):
            summary_text += f"{i}. {job['title']} ({job['reference']})\n"
            summary_text += f"   Department: {job['department']}\n"
            summary_text += f"   Level: {job['salary_band']}\n"
            summary_text += f"   Period: {job['start_date']} - {job['end_date']}\n"
            summary_text += f"   Deadline: {job['deadline']}\n\n"

        return {
            "data": {**state["data"], "csv_file": str(csv_path)},
            "output_data": {
                "summary": summary_text,
                "csv_file": str(csv_path),
                "jobs": jobs,
                "count": len(jobs),
                "new_jobs": new_jobs,
                "skipped_jobs": skipped_jobs
            },
            "messages": [f"CSV created: {csv_path}"],
            "current_step": "generate_output"
        }

    async def handle_error_step(self, state: WorkflowState) -> dict:
        return {
            "output_data": {"error": state.get("error"), "success": False},
            "messages": ["Workflow encountered an error"],
            "current_step": "handle_error"
        }
