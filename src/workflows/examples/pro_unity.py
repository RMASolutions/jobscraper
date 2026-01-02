"""
Pro-Unity Job Scraper Workflow

Scrapes job listings from https://platform.pro-unity.com/
Handles MFA authentication via OTP email (retrieved from M365 mailbox).
"""

from typing import Any
import logging
import csv
from pathlib import Path
from datetime import datetime

from ..base import BaseWorkflow, WorkflowState
from ..registry import register_workflow
from ...browser import get_browser_manager
from ...browser.actions import navigate, click, get_text, get_all_text, fill, wait_for_selector
from ...providers import get_llm_provider
from ...integrations.m365 import M365EmailClient
from ...core.config import settings
from ...db import AsyncSessionLocal, JobRepository, JobSource

logger = logging.getLogger(__name__)


@register_workflow("pro_unity")
class ProUnityWorkflow(BaseWorkflow):
    """
    Workflow to scrape job listings from Pro-Unity platform.

    Steps:
    1. Login with username/password
    2. Handle MFA (retrieve OTP from M365 email)
    3. Navigate to job listings
    4. Scrape jobs from multiple pages
    5. Get details for all jobs
    6. Summarize descriptions with AI
    7. Save to database (skip duplicates)
    8. Output to CSV
    """

    # URLs
    LOGIN_URL = "https://platform.pro-unity.com/login"
    JOBS_URL = "https://platform.pro-unity.com/Freelancer/job-posts"

    # Selectors
    SELECTORS = {
        # Login page - Step 1: Username/Email
        "username_field": "input[placeholder='Your email...']",
        "username_submit": "button.pu-button:has-text('Continue')",

        # Login page - Step 2: Password
        "password_field": "input[placeholder='Your password...']",
        "password_submit": "button.pu-button:has-text('Sign in')",

        # Login page - Step 3: OTP/MFA
        "otp_field": "input.digit-input",
        "otp_submit": "button.pu-button:has-text('Verify')",
        "login_success": ".item-menu",

        # Job list page
        "job_container": ".job-item",
        "job_title": "span.color-text-link",
        "job_link": "span.color-text-link", # Used to extract UUID from data-cy
        "job_client": "pu-company-logo", # Client logo, text extracted from detail
        "job_date": ".job-date",
        "next_page": "pu-pagination .page-box:not(.active):has-text('>')",

        # Job detail page
        "detail_container": "app-ta-job-post-details",
        "description": "pu-accordion:has-text('Details')",
        "skills": "pu-accordion:has-text('Details')", # Skills are inside the same accordion or under a header within it
        "client": "div.job-post-details div:nth-child(2)",
        "detail_content": ".accordion-body",
    }

    # OTP Email Configuration
    OTP_SENDER_CONTAINS = "info@pro-unity.com"
    OTP_SUBJECT_CONTAINS = "ProUnity account security code"

    def __init__(self):
        super().__init__("pro_unity")
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
        """Login to Pro-Unity with MFA via email OTP."""
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

        # Initialize M365 client for OTP retrieval
        m365_client = M365EmailClient(
            tenant_id=settings.m365_tenant_id,
            client_id=settings.m365_client_id,
            client_secret=settings.m365_client_secret,
            user_email=settings.m365_user_email
        )

        browser_manager = await get_browser_manager()

        try:
            async with browser_manager.new_page() as page:
                # Step 1: Navigate to login page
                await navigate(page, self.LOGIN_URL)

                # Handle cookie consent banner (OneTrust)
                await page.wait_for_timeout(2000)  # Wait for banner to appear
                try:
                    # Try clicking accept button with multiple possible selectors
                    for selector in ["#onetrust-accept-btn-handler", "#accept-recommended-btn-handler", "button:has-text('Accept')", "button:has-text('Autoriser')"]:
                        try:
                            btn = page.locator(selector)
                            if await btn.count() > 0 and await btn.first.is_visible():
                                logger.info(f"Clicking cookie consent button: {selector}")
                                await btn.first.click()
                                await page.wait_for_timeout(1000)
                                break
                        except Exception:
                            continue

                    # Fallback: Remove OneTrust overlay via JavaScript
                    await page.evaluate("""
                        const overlay = document.getElementById('onetrust-banner-sdk') || document.getElementById('onetrust-consent-sdk');
                        if (overlay) overlay.remove();
                        const backdrop = document.querySelector('.onetrust-pc-dark-filter');
                        if (backdrop) backdrop.remove();
                    """)
                    logger.info("Removed OneTrust overlay via JavaScript")
                except Exception as e:
                    logger.warning(f"Cookie banner handling: {e}")

                # Step 2: Enter username/email
                await wait_for_selector(page, self.SELECTORS["username_field"])
                await fill(page, self.SELECTORS["username_field"], username)

                # Some sites have a separate "Next" button after email
                if self.SELECTORS.get("username_submit") and self.SELECTORS["username_submit"] != "TODO":
                    await click(page, self.SELECTORS["username_submit"])
                    await page.wait_for_timeout(1000)

                # Step 3: Enter password
                await wait_for_selector(page, self.SELECTORS["password_field"])
                await fill(page, self.SELECTORS["password_field"], password)
                await click(page, self.SELECTORS["password_submit"])

                # Step 4: Handle MFA - Wait for OTP field to appear
                logger.info("Waiting for MFA/OTP prompt...")
                await wait_for_selector(page, self.SELECTORS["otp_field"], timeout=15000)

                # Step 5: Retrieve OTP from email
                logger.info("Retrieving OTP from M365 mailbox...")
                otp_code = await m365_client.wait_for_otp_email(
                    sender_contains=self.OTP_SENDER_CONTAINS,
                    subject_contains=self.OTP_SUBJECT_CONTAINS if self.OTP_SUBJECT_CONTAINS else None,
                    timeout_seconds=120,
                    poll_interval=5
                )

                if not otp_code:
                    return {
                        "data": {**state.get("data", {}), "login_success": False},
                        "error": "Failed to retrieve OTP from email",
                        "messages": ["Login failed: OTP not received"]
                    }

                # Step 6: Enter OTP
                logger.info(f"Entering OTP code...")
                
                # Hande split inputs (e.g. 6 separate fields)
                inputs = await page.query_selector_all(self.SELECTORS["otp_field"])
                if len(inputs) > 1 and len(otp_code) == len(inputs):
                     for i, char in enumerate(otp_code):
                        await inputs[i].fill(char)
                else:
                    await fill(page, self.SELECTORS["otp_field"], otp_code)
                
                await click(page, self.SELECTORS["otp_submit"])

                # Step 7: Wait for login success
                await wait_for_selector(page, self.SELECTORS["login_success"], timeout=15000)

                # Save session
                storage_state = await browser_manager.save_storage_state(page.context)

                return {
                    "data": {
                        **state.get("data", {}),
                        "login_success": True,
                        "storage_state": storage_state,
                    },
                    "messages": ["Successfully logged into Pro-Unity"],
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
            await navigate(page, self.JOBS_URL)

            try:
                await wait_for_selector(page, self.SELECTORS["job_container"], timeout=10000)
            except Exception:
                logger.warning("No jobs table found or timeout.")

            current_page = 1
            while current_page <= max_pages:
                logger.info(f"Scraping page {current_page}")

                rows = await page.query_selector_all(self.SELECTORS["job_container"])

                for row in rows:
                    try:
                        title_el = await row.query_selector(self.SELECTORS["job_title"])
                        
                        title = await title_el.text_content() if title_el else "Unknown"
                        
                        # Extract job URL from data-cy attribute on title
                        link = ""
                        if title_el:
                            data_cy = await title_el.get_attribute("data-cy")
                            if data_cy and "job-post-name-link-" in data_cy:
                                job_id = data_cy.replace("job-post-name-link-", "")
                                link = f"/Freelancer/job-posts/{job_id}"
                        
                        # Fallback to standard <a> if link extraction failed
                        if not link:
                            link_el = await row.query_selector("a")
                            link = await link_el.get_attribute("href") if link_el else ""

                        if title and link:
                            all_jobs.append({
                                "title": title.strip(),
                                "url": link,
                                "client": "N/A", # Populated in detail step
                                "page": current_page
                            })
                    except Exception as e:
                        logger.warning(f"Error parsing row: {e}")

                # Check pagination
                next_btn = await page.query_selector(self.SELECTORS["next_page"])

                if not next_btn:
                    logger.info("No next button found. Ending pagination.")
                    break

                await next_btn.click()
                await page.wait_for_timeout(2000)

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
        base_url = "https://platform.pro-unity.com"

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

                    # Dismiss any Angular CDK overlays/modals that might block interaction
                    await page.evaluate("""
                        // Remove overlay backdrop
                        document.querySelectorAll('.cdk-overlay-backdrop').forEach(el => el.remove());
                        // Remove overlay container content that blocks clicks
                        document.querySelectorAll('.cdk-overlay-container > *').forEach(el => el.remove());
                    """)
                    await page.wait_for_timeout(500)

                    # Extract Client Name from detail page
                    client = "N/A"
                    try:
                        client_el = page.locator(self.SELECTORS["client"]).first
                        if await client_el.count() > 0:
                            client = await client_el.text_content()
                    except Exception as e:
                        logger.warning(f"Failed to extract client: {e}")

                    # Expand description if needed (Accordion)
                    try:
                        desc_accordion = page.locator(self.SELECTORS["description"]).first
                        if await desc_accordion.count() > 0:
                            # Scroll and click to expand
                            await desc_accordion.scroll_into_view_if_needed()
                            # Check if the body is visible; if not, click
                            body_el = desc_accordion.locator(self.SELECTORS["detail_content"])
                            if await body_el.count() == 0 or not await body_el.is_visible():
                                await desc_accordion.click()
                                await page.wait_for_timeout(1000)
                    except Exception as e:
                        logger.warning(f"Failed to expand description: {e}")

                    # Extract raw data from content
                    description_raw = ""
                    try:
                        content_el = page.locator(f"{self.SELECTORS['description']} {self.SELECTORS['detail_content']}").first
                        if await content_el.count() > 0:
                            description_raw = await content_el.text_content()
                        else:
                            # Fallback to container text
                            description_raw = await page.locator(self.SELECTORS["detail_container"]).text_content()
                    except Exception:
                        pass
                    
                    # Skills section (usually inside the same description block or near it)
                    skills = ""
                    # Actually, the skills are often within the description text. 
                    # If there's a specific skills section, we'd add it here.
                    
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
                        "client": client.strip() if client else "N/A",
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
                detailed_jobs, JobSource.PRO_UNITY
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
        csv_filename = f"jobs_pro_unity_{timestamp}.csv"
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

        # Create a brief text summary
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
