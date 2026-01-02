from typing import Any
import logging
import asyncio
import csv
from pathlib import Path
from datetime import datetime

from ..base import BaseWorkflow, WorkflowState
from ..registry import register_workflow
from ...browser import get_browser_manager
from ...browser.actions import navigate, click, get_text, get_all_text, fill, wait_for_selector
from ...providers import get_llm_provider
from ...db import AsyncSessionLocal, JobRepository, JobSource

logger = logging.getLogger(__name__)


@register_workflow("connecting_expertise")
class ConnectingExpertiseWorkflow(BaseWorkflow):
    """
    Workflow to scrape job listings from Connecting Expertise.

    Steps:
    1. Login to connecting-expertise.com (via app.connecting-expertise.com / Keycloak)
    2. Navigate to job listings (Demandes reçues)
    3. Scrape jobs from multiple pages
    4. Use AI to filter relevant jobs
    5. Get details for matching jobs
    6. Generate summary
    """

    # URLs
    LOGIN_URL = "https://app.connecting-expertise.com/"
    JOBS_URL = "https://app.connecting-expertise.com/supplier/supplierrequest"

    # Selectors
    SELECTORS = {
        # Login page
        "username_field": "#username",
        "password_field": "#password",
        "login_button": "#kc-login",
        "login_success": "button.menu-user",  # User menu icon proving login

        # Job list page
        "job_container": "tr.mat-mdc-row",
        "job_title": "td.cdk-column-title",
        "job_link": "td.cdk-column-reference a",
        "job_date": "td.cdk-column-createdAt",
        "job_client": "td.cdk-column-customer",
        "next_page": "button.mat-mdc-paginator-navigation-next:not([disabled])",

        # Job detail page
        # The site uses custom elements prefixed with ce-
        "detail_container": "ce-detail-supplier-request",
        "title_header": "ce-detail-title-supplier-request", 
        "description": "ce-detail-description-supplier-request",
        "budget": "ce-detail-contractual-info-supplier-request",
        "skills": "ce-list-detail-supplier-skill",
        "client_header": "ce-detail-title-supplier-request", # Client often in title component
    }

    def __init__(self):
        super().__init__("connecting_expertise")
        self.llm = get_llm_provider()

    def get_entry_point(self) -> str:
        return "login"

    def define_nodes(self) -> dict[str, callable]:
        return {
            "login": self.login_step,
            "fetch_jobs": self.fetch_jobs_step,
            "get_details": self.get_details_step,
            "save_to_db": self.save_to_db_step,
            "generate_summary": self.generate_summary_step,
            "handle_error": self.handle_error_step,
        }

    def define_edges(self) -> list[tuple]:
        return [
            ("login", self._check_login, {"success": "fetch_jobs", "failure": "handle_error"}),
            ("fetch_jobs", self._has_jobs, {"yes": "get_details", "no": "generate_summary"}),
            ("get_details", "save_to_db"),
            ("save_to_db", "generate_summary"),
            ("generate_summary", "END"),
            ("handle_error", "END"),
        ]

    # Conditions
    def _check_login(self, state: WorkflowState) -> str:
        return "success" if state["data"].get("login_success") else "failure"

    def _has_jobs(self, state: WorkflowState) -> str:
        return "yes" if state["data"].get("all_jobs") else "no"

    # Steps
    async def login_step(self, state: WorkflowState) -> dict:
        """Login to Connecting Expertise."""
        logger.info("Executing login step")

        input_data = state["input_data"]
        username = input_data.get("username")
        password = input_data.get("password")
        
        if not username or not password:
            return {
                "data": {**state.get("data", {}), "login_success": False},
                "error": "Missing username or password",
                "messages": ["Login failed: Missing credentials"]
            }

        browser_manager = await get_browser_manager()

        try:
            async with browser_manager.new_page() as page:
                await navigate(page, self.LOGIN_URL)
                
                # Check if we are redirected to auth page
                # Wait for username field
                await wait_for_selector(page, self.SELECTORS["username_field"])
                
                await fill(page, self.SELECTORS["username_field"], username)
                await fill(page, self.SELECTORS["password_field"], password)
                await click(page, self.SELECTORS["login_button"])

                # Wait for success
                # Note: Site might take a moment to redirect back to app
                await wait_for_selector(page, self.SELECTORS["login_success"], timeout=15000)

                storage_state = await browser_manager.save_storage_state(page.context)

                return {
                    "data": {
                        **state.get("data", {}),
                        "login_success": True,
                        "storage_state": storage_state,
                    },
                    "messages": ["Successfully logged into Connecting Expertise"],
                    "current_step": "login",
                }
        except Exception as e:
            logger.error(f"Login failed: {e}")
            return {
                "data": {**state.get("data", {}), "login_success": False},
                "error": str(e),
                "messages": [f"Login failed: {e}"],
            }

    async def fetch_jobs_step(self, state: WorkflowState) -> dict:
        """Fetch job listings from multiple pages."""
        logger.info("Executing fetch_jobs step")
        
        input_data = state["input_data"]
        max_pages = input_data.get("max_pages", 3)
        storage_state = state["data"].get("storage_state")
        
        all_jobs = []
        browser_manager = await get_browser_manager()

        async with browser_manager.new_page(storage_state=storage_state) as page:
            # Navigate to Jobs URL explicitly to be sure
            await navigate(page, self.JOBS_URL)
            
            # Wait for table to load
            try:
                await wait_for_selector(page, self.SELECTORS["job_container"], timeout=10000)
            except Exception:
                logger.warning("No jobs table found or timeout.")
                # Might be no jobs or different page structure?
            
            current_page = 1
            while current_page <= max_pages:
                logger.info(f"Scraping page {current_page}")
                
                # Get all rows
                rows = await page.query_selector_all(self.SELECTORS["job_container"])
                
                for row in rows:
                    try:
                        title_el = await row.query_selector(self.SELECTORS["job_title"])
                        link_el = await row.query_selector(self.SELECTORS["job_link"])
                        client_el = await row.query_selector(self.SELECTORS["job_client"])
                        
                        title = await title_el.text_content() if title_el else "Unknown"
                        link = await link_el.get_attribute("href") if link_el else ""
                        client = await client_el.text_content() if client_el else ""
                        
                        if title and link:
                            all_jobs.append({
                                "title": title.strip(),
                                "url": link,
                                "client": client.strip(),
                                "page": current_page
                            })
                    except Exception as e:
                        logger.warning(f"Error parsing row: {e}")

                # Check pagination
                next_btn = await page.query_selector(self.SELECTORS["next_page"])
                
                # If no next button or it's disabled (though the selector :not([disabled]) handles that mostly)
                if not next_btn:
                    logger.info("No next button found or disabled. Ending pagination.")
                    break
                    
                # Setup wait for navigation or table update
                # Angular apps often don't reload the page, they just update the DOM.
                # simpler approach: click and wait a bit
                await next_btn.click()
                await page.wait_for_timeout(2000) # Give Angular time to update table
                
                current_page += 1

        return {
            "data": {**state["data"], "all_jobs": all_jobs},
            "messages": [f"Fetched {len(all_jobs)} jobs from {current_page} pages"],
            "current_step": "fetch_jobs",
        }

    async def get_details_step(self, state: WorkflowState) -> dict:
        """Get details for all jobs and summarize descriptions with AI."""
        logger.info("Executing get_details step")

        all_jobs = state["data"].get("all_jobs", [])
        storage_state = state["data"].get("storage_state")
        base_url = "https://app.connecting-expertise.com"

        detailed_jobs = []
        browser_manager = await get_browser_manager()

        async with browser_manager.new_page(storage_state=storage_state) as page:
            for job in all_jobs:
                try:
                    url = job["url"]
                    if not url.startswith("http"):
                        url = base_url + url if url.startswith("/") else f"{base_url}/{url}"

                    await navigate(page, url)
                    await wait_for_selector(page, self.SELECTORS["detail_container"], timeout=10000)

                    # Extract raw data
                    description_raw = await get_text(page, self.SELECTORS["description"])
                    skills = await get_text(page, self.SELECTORS["skills"])

                    # Use AI to summarize the description
                    description_summary = "N/A"
                    if description_raw:
                        try:
                            summary_prompt = f"""Summarize this job description in 2-3 sentences. Focus on:
- Main responsibilities
- Required experience level
- Key technologies/skills

Description:
{description_raw[:3000]}

Provide a concise summary:"""

                            summary_response = await self.llm.generate(
                                prompt=summary_prompt,
                                system_prompt="You are a concise job description summarizer. Be brief and factual.",
                                max_tokens=200
                            )
                            description_summary = summary_response.content.strip()
                        except Exception as e:
                            logger.warning(f"Failed to summarize description: {e}")
                            description_summary = description_raw[:300] + "..."

                    detailed_jobs.append({
                        **job,
                        "description_summary": description_summary,
                        "skills": skills or "N/A"
                    })

                except Exception as e:
                    logger.warning(f"Failed to get details for {job['title']}: {e}")
                    detailed_jobs.append({
                        **job,
                        "description_summary": "Error fetching details",
                        "skills": "N/A",
                        "error": str(e)
                    })

        return {
            "data": {**state["data"], "detailed_jobs": detailed_jobs},
            "messages": [f"Scraped and summarized {len(detailed_jobs)} job details"],
            "current_step": "get_details"
        }

    async def save_to_db_step(self, state: WorkflowState) -> dict:
        """Save jobs to database, skipping duplicates."""
        logger.info("Executing save_to_db step")

        detailed_jobs = state["data"].get("detailed_jobs", [])

        if not detailed_jobs:
            return {
                "data": {**state["data"], "new_jobs": 0, "skipped_jobs": 0},
                "messages": ["No jobs to save to database"],
                "current_step": "save_to_db"
            }

        async with AsyncSessionLocal() as session:
            repo = JobRepository(session)
            new_count, skipped_count = await repo.save_jobs_batch(
                detailed_jobs, JobSource.CONNECTING_EXPERTISE
            )

        logger.info(f"Saved {new_count} new jobs, skipped {skipped_count} duplicates")

        return {
            "data": {**state["data"], "new_jobs": new_count, "skipped_jobs": skipped_count},
            "messages": [f"Saved {new_count} new jobs to DB, skipped {skipped_count} duplicates"],
            "current_step": "save_to_db"
        }

    async def generate_summary_step(self, state: WorkflowState) -> dict:
        """Generate CSV file with job summary."""
        logger.info("Executing generate_summary step")
        detailed_jobs = state["data"].get("detailed_jobs", [])
        output_dir = state["input_data"].get("output_dir", ".")

        if not detailed_jobs:
            return {
                "output_data": {"summary": "No jobs found.", "csv_file": None},
                "messages": ["No jobs to summarize."],
                "current_step": "generate_summary"
            }

        # Generate CSV file
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        csv_filename = f"jobs_connecting_expertise_{timestamp}.csv"
        csv_path = Path(output_dir) / csv_filename

        csv_headers = ["Title", "Client", "Skills", "Description Summary", "URL"]

        with open(csv_path, "w", newline="", encoding="utf-8") as csvfile:
            writer = csv.writer(csvfile)
            writer.writerow(csv_headers)

            for job in detailed_jobs:
                writer.writerow([
                    job.get("title", "N/A"),
                    job.get("client", "N/A"),
                    job.get("skills", "N/A"),
                    job.get("description_summary", "N/A"),
                    job.get("url", "N/A")
                ])

        logger.info(f"CSV file created: {csv_path}")

        # Create a brief text summary as well
        new_jobs = state["data"].get("new_jobs", 0)
        skipped_jobs = state["data"].get("skipped_jobs", 0)

        summary_text = f"Found {len(detailed_jobs)} jobs:\n"
        summary_text += f"  - New jobs saved to DB: {new_jobs}\n"
        summary_text += f"  - Duplicates skipped: {skipped_jobs}\n\n"
        for i, job in enumerate(detailed_jobs, 1):
            summary_text += f"{i}. {job['title']} ({job.get('client', 'N/A')})\n"

        return {
            "data": {**state["data"], "csv_file": str(csv_path)},
            "output_data": {
                "summary": summary_text,
                "csv_file": str(csv_path),
                "jobs": detailed_jobs,
                "count": len(detailed_jobs),
                "new_jobs": new_jobs,
                "skipped_jobs": skipped_jobs
            },
            "messages": [f"CSV created: {csv_path}"],
            "current_step": "generate_summary"
        }

    async def handle_error_step(self, state: WorkflowState) -> dict:
        return {
            "output_data": {"error": state.get("error"), "success": False},
            "messages": ["Workflow encountered an error"],
            "current_step": "handle_error"
        }
