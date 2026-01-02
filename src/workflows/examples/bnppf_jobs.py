"""
BNP Paribas Fortis Job Email Workflow

Reads job opportunity emails from cces@bnpparibasfortis.com,
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


@register_workflow("bnppf_jobs")
class BNPPFJobsWorkflow(BaseWorkflow):
    """
    Workflow to extract job listings from BNPP Fortis emails.

    Steps:
    1. Read emails from cces@bnpparibasfortis.com (today or specified date range)
    2. Parse job details from email content
    3. Summarize descriptions with AI
    4. Save to database (skip duplicates)
    5. Output to CSV
    """

    # Email configuration
    SENDER_EMAIL = "cces@bnpparibasfortis.com"
    SUBJECT_PATTERN = r"New BNP Paribas Fortis request for external staff"

    def __init__(self):
        super().__init__("bnppf_jobs")
        self.llm = get_llm_provider()

    def get_entry_point(self) -> str:
        return "fetch_emails"

    def define_nodes(self) -> dict[str, callable]:
        return {
            "fetch_emails": self.fetch_emails_step,
            "parse_jobs": self.parse_jobs_step,
            "summarize_jobs": self.summarize_jobs_step,
            "save_to_db": self.save_to_db_step,
            "generate_output": self.generate_output_step,
            "handle_error": self.handle_error_step,
        }

    def define_edges(self) -> list[tuple]:
        return [
            ("fetch_emails", self._has_emails, {"yes": "parse_jobs", "no": "generate_output"}),
            ("parse_jobs", "summarize_jobs"),
            ("summarize_jobs", "save_to_db"),
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
        days_back = input_data.get("days_back", 1)  # Default: today's emails

        # Initialize M365 client
        m365_client = M365EmailClient(
            tenant_id=settings.m365_tenant_id,
            client_id=settings.m365_client_id,
            client_secret=settings.m365_client_secret,
            user_email=settings.m365_user_email
        )

        try:
            # Get access token
            token = await m365_client._get_token()

            # Calculate date range
            from_date = datetime.utcnow() - timedelta(days=days_back)

            # Fetch emails from BNPPF
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

            # Filter for job emails (check subject pattern)
            job_emails = [
                email for email in emails
                if re.search(self.SUBJECT_PATTERN, email.get("subject", ""), re.IGNORECASE)
            ]

            logger.info(f"Found {len(job_emails)} job emails from BNPPF")

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

                # Extract job reference from subject
                ref_match = re.search(r'\(([A-Z]{3}\d+)\)', subject)
                job_reference = ref_match.group(1) if ref_match else "N/A"

                # Extract job title from subject
                title_match = re.search(r'external staff\s*:\s*(.+?)\s*\([A-Z]{3}\d+\)', subject, re.IGNORECASE)
                job_title = title_match.group(1).strip() if title_match else self._extract_field(body_text, "Job title")

                # Parse structured fields from body
                job_data = {
                    "reference": job_reference,
                    "title": job_title or "N/A",
                    "location": self._extract_field(body_text, "Work location"),
                    "start_date": self._extract_field(body_text, "Start date"),
                    "end_date": self._extract_field(body_text, "End date"),
                    "description": self._extract_section(body_text, "Description", "Language requirements"),
                    "languages": self._extract_field(body_text, "Language requirements"),
                    "education": self._extract_field(body_text, "Education"),
                    "experience": self._extract_section(body_text, "Required experience / knowledge", "Technical experience"),
                    "technical_skills": self._extract_section(body_text, "Technical experience", "Business experience"),
                    "telework": self._extract_field(body_text, "Telework"),
                    "received_date": received_date[:10] if received_date else "N/A",
                    "raw_body": body_text,  # Keep for summarization
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

    def _extract_field(self, text: str, field_name: str) -> str:
        """Extract a single-line field value."""
        pattern = rf"{re.escape(field_name)}\s*[:\-]?\s*(.+?)(?:\n|$)"
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            value = match.group(1).strip()
            # Clean up common patterns
            value = re.sub(r'\s+', ' ', value)
            return value[:200] if value else "N/A"
        return "N/A"

    def _extract_section(self, text: str, start_marker: str, end_marker: str) -> str:
        """Extract a multi-line section between markers."""
        pattern = rf"{re.escape(start_marker)}.*?(?:\n|$)(.*?)(?={re.escape(end_marker)}|$)"
        match = re.search(pattern, text, re.IGNORECASE | re.DOTALL)
        if match:
            value = match.group(1).strip()
            # Clean up whitespace
            value = re.sub(r'\n\s*\n', '\n', value)
            return value[:3000] if value else "N/A"
        return "N/A"

    async def summarize_jobs_step(self, state: WorkflowState) -> dict:
        """Summarize job descriptions with AI."""
        logger.info("Executing summarize_jobs step")

        parsed_jobs = state["data"].get("parsed_jobs", [])
        summarized_jobs = []

        for job in parsed_jobs:
            description = job.get("description", "")
            raw_body = job.get("raw_body", "")

            # Use AI to summarize
            summary = "N/A"
            if description and description != "N/A":
                try:
                    prompt = f"""Summarize this job description in 2-3 sentences. Focus on:
- Main role/responsibilities
- Key technologies or domain
- Experience level required

Description:
{description[:2500]}

Provide a concise summary:"""

                    response = await self.llm.generate(
                        prompt=prompt,
                        system_prompt="You are a concise job description summarizer. Be brief and factual.",
                        max_tokens=200
                    )
                    summary = response.content.strip()
                except Exception as e:
                    logger.warning(f"Failed to summarize: {e}")
                    summary = description[:300] + "..." if len(description) > 300 else description

            summarized_jobs.append({
                "reference": job.get("reference", "N/A"),
                "title": job.get("title", "N/A"),
                "location": job.get("location", "N/A"),
                "start_date": job.get("start_date", "N/A"),
                "end_date": job.get("end_date", "N/A"),
                "description_summary": summary,
                "languages": job.get("languages", "N/A"),
                "education": job.get("education", "N/A"),
                "telework": job.get("telework", "N/A"),
                "received_date": job.get("received_date", "N/A"),
            })

        return {
            "data": {**state["data"], "summarized_jobs": summarized_jobs},
            "messages": [f"Summarized {len(summarized_jobs)} job descriptions"],
            "current_step": "summarize_jobs",
        }

    async def save_to_db_step(self, state: WorkflowState) -> dict:
        """Save jobs to database, skipping duplicates."""
        logger.info("Executing save_to_db step")

        summarized_jobs = state["data"].get("summarized_jobs", [])

        if not summarized_jobs:
            return {
                "data": {**state["data"], "new_jobs": 0, "skipped_jobs": 0},
                "messages": ["No jobs to save to database"],
                "current_step": "save_to_db"
            }

        async with AsyncSessionLocal() as session:
            repo = JobRepository(session)
            new_count, skipped_count = await repo.save_jobs_batch(
                summarized_jobs, JobSource.BNPPF
            )

        logger.info(f"Saved {new_count} new jobs, skipped {skipped_count} duplicates")

        return {
            "data": {**state["data"], "new_jobs": new_count, "skipped_jobs": skipped_count},
            "messages": [f"Saved {new_count} new jobs to DB, skipped {skipped_count} duplicates"],
            "current_step": "save_to_db"
        }

    async def generate_output_step(self, state: WorkflowState) -> dict:
        """Generate CSV file with job listings."""
        logger.info("Executing generate_output step")

        jobs = state["data"].get("summarized_jobs", [])
        output_dir = state["input_data"].get("output_dir", ".")

        if not jobs:
            return {
                "output_data": {"summary": "No job emails found.", "csv_file": None, "count": 0},
                "messages": ["No jobs to output."],
                "current_step": "generate_output"
            }

        # Generate CSV file
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        csv_filename = f"jobs_bnppf_{timestamp}.csv"
        csv_path = Path(output_dir) / csv_filename

        csv_headers = [
            "Reference", "Title", "Location", "Start Date", "End Date",
            "Description Summary", "Languages", "Education", "Telework", "Received Date"
        ]

        with open(csv_path, "w", newline="", encoding="utf-8") as csvfile:
            writer = csv.writer(csvfile)
            writer.writerow(csv_headers)

            for job in jobs:
                writer.writerow([
                    job.get("reference", "N/A"),
                    job.get("title", "N/A"),
                    job.get("location", "N/A"),
                    job.get("start_date", "N/A"),
                    job.get("end_date", "N/A"),
                    job.get("description_summary", "N/A"),
                    job.get("languages", "N/A"),
                    job.get("education", "N/A"),
                    job.get("telework", "N/A"),
                    job.get("received_date", "N/A"),
                ])

        logger.info(f"CSV file created: {csv_path}")

        # Create text summary
        new_jobs = state["data"].get("new_jobs", 0)
        skipped_jobs = state["data"].get("skipped_jobs", 0)

        summary_text = f"Found {len(jobs)} BNPP Fortis job(s):\n"
        summary_text += f"  - New jobs saved to DB: {new_jobs}\n"
        summary_text += f"  - Duplicates skipped: {skipped_jobs}\n\n"
        for i, job in enumerate(jobs, 1):
            summary_text += f"{i}. {job['title']} ({job['reference']})\n"
            summary_text += f"   Location: {job['location']}\n"
            summary_text += f"   Period: {job['start_date']} - {job['end_date']}\n\n"

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
