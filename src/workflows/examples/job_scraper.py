from typing import Any
import logging

from ..base import BaseWorkflow, WorkflowState
from ..registry import register_workflow
from ...browser import get_browser_manager
from ...browser.actions import navigate, click, get_text, get_all_text, fill, wait_for_selector
from ...providers import get_llm_provider

logger = logging.getLogger(__name__)


@register_workflow("job_scraper")
class JobScraperWorkflow(BaseWorkflow):
    """
    Example workflow: Scrape job listings from a freelance platform.

    Steps:
    1. Login to the platform
    2. Navigate to job listings
    3. Scrape job list (multiple pages)
    4. Use AI to filter relevant jobs
    5. Get details for relevant jobs
    6. Generate summary
    7. Send notification
    """

    def __init__(self):
        super().__init__("job_scraper")
        self.llm = get_llm_provider()

    def get_entry_point(self) -> str:
        return "login"

    def define_nodes(self) -> dict[str, callable]:
        return {
            "login": self.login_step,
            "fetch_job_list": self.fetch_job_list_step,
            "filter_jobs": self.filter_jobs_step,
            "get_job_details": self.get_job_details_step,
            "generate_summary": self.generate_summary_step,
            "send_notification": self.send_notification_step,
            "handle_error": self.handle_error_step,
        }

    def define_edges(self) -> list[tuple]:
        return [
            ("login", self._check_login_success, {
                "success": "fetch_job_list",
                "failure": "handle_error",
            }),
            ("fetch_job_list", "filter_jobs"),
            ("filter_jobs", self._check_has_relevant_jobs, {
                "has_jobs": "get_job_details",
                "no_jobs": "generate_summary",
            }),
            ("get_job_details", "generate_summary"),
            ("generate_summary", "send_notification"),
            ("send_notification", "END"),
            ("handle_error", "END"),
        ]

    # Condition functions
    def _check_login_success(self, state: WorkflowState) -> str:
        return "success" if state["data"].get("login_success") else "failure"

    def _check_has_relevant_jobs(self, state: WorkflowState) -> str:
        relevant_jobs = state["data"].get("relevant_jobs", [])
        return "has_jobs" if relevant_jobs else "no_jobs"

    # Node handlers
    async def login_step(self, state: WorkflowState) -> dict:
        """Login to the freelance platform."""
        logger.info("Executing login step")

        input_data = state["input_data"]
        platform_url = input_data.get("platform_url")
        username = input_data.get("username")
        password = input_data.get("password")

        # Get login selectors from config
        selectors = input_data.get("selectors", {})
        login_url = input_data.get("login_url", platform_url)

        browser_manager = await get_browser_manager()

        try:
            async with browser_manager.new_page() as page:
                await navigate(page, login_url)

                # Fill login form
                await fill(page, selectors.get("username", "#username"), username)
                await fill(page, selectors.get("password", "#password"), password)
                await click(page, selectors.get("submit", "button[type='submit']"))

                # Wait for login success indicator
                success_selector = selectors.get("success_indicator", ".dashboard")
                await wait_for_selector(page, success_selector, timeout=10000)

                # Save session state for subsequent pages
                storage_state = await browser_manager.save_storage_state(page.context)

                return {
                    "data": {
                        **state.get("data", {}),
                        "login_success": True,
                        "storage_state": storage_state,
                    },
                    "messages": [f"Successfully logged into {platform_url}"],
                    "current_step": "login",
                }
        except Exception as e:
            logger.error(f"Login failed: {e}")
            return {
                "data": {**state.get("data", {}), "login_success": False},
                "error": str(e),
                "messages": [f"Login failed: {e}"],
            }

    async def fetch_job_list_step(self, state: WorkflowState) -> dict:
        """Fetch job listings from multiple pages."""
        logger.info("Executing fetch_job_list step")

        input_data = state["input_data"]
        jobs_url = input_data.get("jobs_url")
        max_pages = input_data.get("max_pages", 5)
        selectors = input_data.get("selectors", {})

        storage_state = state["data"].get("storage_state")
        all_jobs = []

        browser_manager = await get_browser_manager()

        async with browser_manager.new_page(storage_state=storage_state) as page:
            current_page = 1

            while current_page <= max_pages:
                page_url = f"{jobs_url}?page={current_page}"
                await navigate(page, page_url)

                # Get job listings
                job_selector = selectors.get("job_item", ".job-listing")
                title_selector = selectors.get("job_title", ".job-title")
                link_selector = selectors.get("job_link", "a")

                job_elements = await page.query_selector_all(job_selector)

                for job_el in job_elements:
                    title_el = await job_el.query_selector(title_selector)
                    link_el = await job_el.query_selector(link_selector)

                    title = await title_el.text_content() if title_el else ""
                    link = await link_el.get_attribute("href") if link_el else ""

                    if title:
                        all_jobs.append({
                            "title": title.strip(),
                            "url": link,
                            "page": current_page,
                        })

                # Check for next page
                next_selector = selectors.get("next_page", ".pagination .next")
                next_button = await page.query_selector(next_selector)

                if not next_button:
                    break

                current_page += 1

        return {
            "data": {**state["data"], "all_jobs": all_jobs},
            "messages": [f"Fetched {len(all_jobs)} jobs from {current_page} pages"],
            "current_step": "fetch_job_list",
        }

    async def filter_jobs_step(self, state: WorkflowState) -> dict:
        """Use AI to filter relevant jobs based on criteria."""
        logger.info("Executing filter_jobs step")

        all_jobs = state["data"].get("all_jobs", [])
        criteria = state["input_data"].get("filter_criteria", "")

        if not all_jobs:
            return {
                "data": {**state["data"], "relevant_jobs": []},
                "messages": ["No jobs to filter"],
            }

        # Use LLM to evaluate job relevance
        job_titles = [job["title"] for job in all_jobs]
        prompt = f"""Analyze these job titles and identify which ones match the following criteria:

Criteria: {criteria}

Job titles:
{chr(10).join(f'{i+1}. {title}' for i, title in enumerate(job_titles))}

Return the numbers of relevant jobs as a JSON array, e.g., [1, 3, 5].
Only include jobs that strongly match the criteria."""

        try:
            result = await self.llm.generate_structured(
                prompt=prompt,
                response_schema={"type": "array", "items": {"type": "integer"}},
                system_prompt="You are a job matching assistant. Be selective and only match truly relevant jobs.",
            )

            relevant_indices = set(result)
            relevant_jobs = [
                job for i, job in enumerate(all_jobs) if (i + 1) in relevant_indices
            ]

            return {
                "data": {**state["data"], "relevant_jobs": relevant_jobs},
                "messages": [f"Found {len(relevant_jobs)} relevant jobs out of {len(all_jobs)}"],
                "current_step": "filter_jobs",
            }
        except Exception as e:
            logger.error(f"Job filtering failed: {e}")
            # On error, return all jobs
            return {
                "data": {**state["data"], "relevant_jobs": all_jobs},
                "messages": [f"Filtering failed, returning all {len(all_jobs)} jobs"],
                "error": str(e),
            }

    async def get_job_details_step(self, state: WorkflowState) -> dict:
        """Get detailed information for relevant jobs."""
        logger.info("Executing get_job_details step")

        relevant_jobs = state["data"].get("relevant_jobs", [])
        selectors = state["input_data"].get("selectors", {})
        storage_state = state["data"].get("storage_state")
        base_url = state["input_data"].get("platform_url", "")

        detailed_jobs = []
        browser_manager = await get_browser_manager()

        async with browser_manager.new_page(storage_state=storage_state) as page:
            for job in relevant_jobs:
                try:
                    job_url = job["url"]
                    if not job_url.startswith("http"):
                        job_url = f"{base_url.rstrip('/')}/{job_url.lstrip('/')}"

                    await navigate(page, job_url)

                    # Extract job details
                    description = await get_text(
                        page, selectors.get("description", ".job-description")
                    )
                    budget = await get_text(
                        page, selectors.get("budget", ".budget")
                    )
                    skills = await get_all_text(
                        page, selectors.get("skills", ".skill-tag")
                    )

                    detailed_jobs.append({
                        **job,
                        "description": description[:1000],  # Truncate
                        "budget": budget,
                        "skills": skills,
                    })
                except Exception as e:
                    logger.warning(f"Failed to get details for {job['title']}: {e}")
                    detailed_jobs.append({**job, "error": str(e)})

        return {
            "data": {**state["data"], "detailed_jobs": detailed_jobs},
            "messages": [f"Retrieved details for {len(detailed_jobs)} jobs"],
            "current_step": "get_job_details",
        }

    async def generate_summary_step(self, state: WorkflowState) -> dict:
        """Generate a summary of relevant jobs using AI."""
        logger.info("Executing generate_summary step")

        detailed_jobs = state["data"].get("detailed_jobs", [])
        relevant_jobs = state["data"].get("relevant_jobs", [])

        jobs_to_summarize = detailed_jobs if detailed_jobs else relevant_jobs

        if not jobs_to_summarize:
            summary = "No relevant jobs found matching your criteria."
        else:
            jobs_text = "\n\n".join(
                f"**{job['title']}**\n"
                f"Budget: {job.get('budget', 'N/A')}\n"
                f"Skills: {', '.join(job.get('skills', []))}\n"
                f"Description: {job.get('description', 'N/A')[:500]}"
                for job in jobs_to_summarize
            )

            prompt = f"""Create a concise summary of these job opportunities for a freelancer.
Highlight the most promising ones and explain why.

Jobs:
{jobs_text}

Provide a brief, actionable summary."""

            response = await self.llm.generate(
                prompt=prompt,
                system_prompt="You are a helpful assistant summarizing job opportunities.",
            )
            summary = response.content

        return {
            "data": {**state["data"], "summary": summary},
            "output_data": {
                "summary": summary,
                "job_count": len(jobs_to_summarize),
                "jobs": jobs_to_summarize,
            },
            "messages": ["Generated job summary"],
            "current_step": "generate_summary",
        }

    async def send_notification_step(self, state: WorkflowState) -> dict:
        """Send notification with job summary."""
        logger.info("Executing send_notification step")

        # Placeholder: In real implementation, integrate with email/Slack/etc.
        summary = state["data"].get("summary", "")
        notification_config = state["input_data"].get("notification", {})

        # Log the notification (replace with actual sending logic)
        logger.info(f"Notification would be sent to: {notification_config.get('email', 'admin')}")
        logger.info(f"Summary: {summary[:200]}...")

        return {
            "data": {**state["data"], "notification_sent": True},
            "messages": ["Notification sent successfully"],
            "current_step": "send_notification",
        }

    async def handle_error_step(self, state: WorkflowState) -> dict:
        """Handle workflow errors."""
        logger.error(f"Workflow error: {state.get('error')}")

        return {
            "output_data": {
                "success": False,
                "error": state.get("error"),
            },
            "messages": [f"Workflow failed: {state.get('error')}"],
            "current_step": "handle_error",
        }
