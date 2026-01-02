"""
AG Insurance Job Email Workflow

Reads job opportunity emails from externis@email.aginsurance.be,
extracts job details from the table format, and outputs to CSV.
"""

from typing import Any
import logging
import csv
import re
from pathlib import Path
from datetime import datetime, timedelta

from ..base import BaseWorkflow, WorkflowState
from ..registry import register_workflow
from ...integrations.m365 import M365EmailClient
from ...core.config import settings
from ...db import AsyncSessionLocal, JobRepository, JobSource

logger = logging.getLogger(__name__)


@register_workflow("ag_insurance")
class AGInsuranceWorkflow(BaseWorkflow):
    """
    Workflow to extract job listings from AG Insurance emails.

    Steps:
    1. Read emails from externis@email.aginsurance.be (today or specified date range)
    2. Parse job details from email table content
    3. Save to database (skip duplicates based on reference)
    4. Output to CSV
    """

    # Email configuration
    SENDER_EMAIL = "externis@email.aginsurance.be"
    SUBJECT_PATTERN = r"AG Insurance is currently looking for"

    def __init__(self):
        super().__init__("ag_insurance")

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

            # Filter for job emails (must contain job table)
            job_emails = [
                email for email in emails
                if "AG Insurance is currently looking for" in email.get("body", {}).get("content", "")
            ]

            logger.info(f"Found {len(job_emails)} job emails from AG Insurance")

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
        seen_references = set()  # Track references within this batch

        for email in raw_emails:
            try:
                body_content = email.get("body", {}).get("content", "")
                body_type = email.get("body", {}).get("contentType", "text")
                received_date = email.get("receivedDateTime", "")

                # Convert HTML to text if needed
                if body_type.lower() == "html":
                    from bs4 import BeautifulSoup
                    soup = BeautifulSoup(body_content, "html.parser")

                    # Try to find the table with job data
                    # The table has headers: Reference, #Required consultants, Job Description, Client, #Months, Location
                    tables = soup.find_all("table")

                    for table in tables:
                        rows = table.find_all("tr")
                        header_found = False

                        for row in rows:
                            cells = row.find_all(["td", "th"])
                            cell_texts = [cell.get_text(strip=True) for cell in cells]

                            # Check if this is the header row
                            if any("Reference" in text for text in cell_texts):
                                header_found = True
                                continue

                            # Parse data rows (after header)
                            if header_found and len(cell_texts) >= 5:
                                reference = cell_texts[0].strip()

                                # Skip if no reference or already seen
                                if not reference or reference in seen_references:
                                    continue

                                # Skip header-like rows
                                if reference.lower() == "reference" or "#" in reference:
                                    continue

                                seen_references.add(reference)

                                # Parse the row
                                # Format: Reference, #Required, Job Description, Client, #Months, Location
                                job_data = {
                                    "reference": reference,
                                    "required_consultants": cell_texts[1].strip() if len(cell_texts) > 1 else "1",
                                    "title": cell_texts[2].strip() if len(cell_texts) > 2 else "N/A",
                                    "client": cell_texts[3].strip() if len(cell_texts) > 3 else "N/A",
                                    "duration_months": cell_texts[4].strip() if len(cell_texts) > 4 else "N/A",
                                    "location": cell_texts[5].strip() if len(cell_texts) > 5 else "N/A",
                                    "received_date": received_date[:10] if received_date else "N/A",
                                }

                                jobs.append(job_data)
                                logger.info(f"Parsed job: {job_data['title']} ({reference})")

                    # Fallback: try to parse from plain text if no table found
                    if not jobs:
                        body_text = soup.get_text(separator="\n")
                        jobs.extend(self._parse_from_text(body_text, received_date, seen_references))
                else:
                    # Plain text email
                    jobs.extend(self._parse_from_text(body_content, received_date, seen_references))

            except Exception as e:
                logger.warning(f"Failed to parse email: {e}")

        return {
            "data": {**state["data"], "parsed_jobs": jobs},
            "messages": [f"Parsed {len(jobs)} jobs from emails"],
            "current_step": "parse_jobs",
        }

    def _parse_from_text(self, text: str, received_date: str, seen_references: set) -> list:
        """Fallback parser for plain text emails."""
        jobs = []

        # Try to find job patterns in text
        # Reference pattern like "3410INFPM" followed by job info
        lines = text.split("\n")

        for i, line in enumerate(lines):
            # Look for reference pattern (alphanumeric, typically starts with digits)
            ref_match = re.match(r'^(\d{4}[A-Z]{3,})', line.strip())
            if ref_match:
                reference = ref_match.group(1)

                if reference in seen_references:
                    continue

                seen_references.add(reference)

                # Try to extract info from surrounding text
                remaining = line[len(reference):].strip()
                parts = [p.strip() for p in remaining.split("\t") if p.strip()]

                if not parts:
                    # Try splitting by multiple spaces
                    parts = [p.strip() for p in re.split(r'\s{2,}', remaining) if p.strip()]

                job_data = {
                    "reference": reference,
                    "required_consultants": parts[0] if len(parts) > 0 else "1",
                    "title": parts[1] if len(parts) > 1 else "N/A",
                    "client": parts[2] if len(parts) > 2 else "N/A",
                    "duration_months": parts[3] if len(parts) > 3 else "N/A",
                    "location": parts[4] if len(parts) > 4 else "N/A",
                    "received_date": received_date[:10] if received_date else "N/A",
                }

                jobs.append(job_data)
                logger.info(f"Parsed job from text: {job_data['title']} ({reference})")

        return jobs

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
                parsed_jobs, JobSource.AG_INSURANCE
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
        csv_filename = f"jobs_ag_insurance_{timestamp}.csv"
        csv_path = Path(output_dir) / csv_filename

        csv_headers = [
            "Reference", "Title", "Client", "Required Consultants",
            "Duration (Months)", "Location", "Received Date"
        ]

        with open(csv_path, "w", newline="", encoding="utf-8") as csvfile:
            writer = csv.writer(csvfile)
            writer.writerow(csv_headers)

            for job in jobs:
                writer.writerow([
                    job.get("reference", "N/A"),
                    job.get("title", "N/A"),
                    job.get("client", "N/A"),
                    job.get("required_consultants", "N/A"),
                    job.get("duration_months", "N/A"),
                    job.get("location", "N/A"),
                    job.get("received_date", "N/A"),
                ])

        logger.info(f"CSV file created: {csv_path}")

        # Create text summary
        new_jobs = state["data"].get("new_jobs", 0)
        skipped_jobs = state["data"].get("skipped_jobs", 0)

        summary_text = f"Found {len(jobs)} AG Insurance job(s):\n"
        summary_text += f"  - New jobs saved to DB: {new_jobs}\n"
        summary_text += f"  - Duplicates skipped: {skipped_jobs}\n\n"
        for i, job in enumerate(jobs, 1):
            summary_text += f"{i}. {job['title']} ({job['reference']})\n"
            summary_text += f"   Client: {job['client']}\n"
            summary_text += f"   Location: {job['location']}\n"
            summary_text += f"   Duration: {job['duration_months']} months\n\n"

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
