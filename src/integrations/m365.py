"""
Microsoft 365 Email Client for OTP retrieval.

Uses Microsoft Graph API to read emails from a user's mailbox.
This is used for MFA authentication where OTP is sent via email.

REQUIRED AZURE AD APP SETUP:
1. Go to Azure Portal > Azure Active Directory > App registrations
2. Create new registration (e.g., "Workflow-OTP-Reader")
3. Note the Application (client) ID and Directory (tenant) ID
4. Go to "Certificates & secrets" > New client secret
5. Go to "API permissions" > Add permission > Microsoft Graph > Application permissions
6. Add: Mail.Read (Read mail in all mailboxes)
7. Click "Grant admin consent" (requires admin)
"""

import httpx
from datetime import datetime, timedelta
import re
import asyncio
import logging
from html import unescape
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)


class M365EmailClient:
    """Client to read emails from M365 using Microsoft Graph API."""

    GRAPH_URL = "https://graph.microsoft.com/v1.0"

    def __init__(
        self,
        tenant_id: str,
        client_id: str,
        client_secret: str,
        user_email: str
    ):
        """
        Initialize with Azure AD app credentials.

        Args:
            tenant_id: Azure AD tenant ID
            client_id: Azure AD app client ID
            client_secret: Azure AD app client secret
            user_email: Email address of the user whose mailbox to read
        """
        self.tenant_id = tenant_id
        self.client_id = client_id
        self.client_secret = client_secret
        self.user_email = user_email
        self._access_token = None
        self._token_expires = None

    async def _get_token(self) -> str:
        """Get or refresh access token using client credentials flow."""
        if self._access_token and self._token_expires and datetime.now() < self._token_expires:
            return self._access_token

        token_url = f"https://login.microsoftonline.com/{self.tenant_id}/oauth2/v2.0/token"

        async with httpx.AsyncClient() as client:
            response = await client.post(token_url, data={
                "grant_type": "client_credentials",
                "client_id": self.client_id,
                "client_secret": self.client_secret,
                "scope": "https://graph.microsoft.com/.default"
            })
            response.raise_for_status()
            data = response.json()

            self._access_token = data["access_token"]
            # Refresh 60 seconds before expiry
            self._token_expires = datetime.now() + timedelta(seconds=data["expires_in"] - 60)

            logger.info("Obtained new Graph API access token")
            return self._access_token

    async def wait_for_otp_email(
        self,
        sender_contains: str,
        subject_contains: str | None = None,
        timeout_seconds: int = 120,
        poll_interval: int = 5,
        otp_pattern: str | None = None
    ) -> str | None:
        """
        Wait for OTP email and extract the code.

        Args:
            sender_contains: Part of sender email to match (e.g., "pro-unity", "noreply")
            subject_contains: Part of subject to match (e.g., "verification", "code")
            timeout_seconds: Max time to wait for email (default 2 minutes)
            poll_interval: Seconds between mailbox checks
            otp_pattern: Custom regex pattern to extract OTP (optional)

        Returns:
            The OTP code extracted from the email, or None if not found
        """
        start_time = datetime.now()
        # Look for emails received in the last 2 minutes
        search_start = datetime.utcnow() - timedelta(minutes=2)

        logger.info(f"Waiting for OTP email from sender containing '{sender_contains}'...")

        while (datetime.now() - start_time).total_seconds() < timeout_seconds:
            try:
                token = await self._get_token()

                # Build filter query
                filter_parts = [
                    f"receivedDateTime ge {search_start.isoformat()}Z"
                ]

                url = f"{self.GRAPH_URL}/users/{self.user_email}/messages"
                params = {
                    "$filter": " and ".join(filter_parts),
                    "$orderby": "receivedDateTime desc",
                    "$top": 10,
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

                for email in data.get("value", []):
                    sender = email.get("from", {}).get("emailAddress", {}).get("address", "")
                    subject = email.get("subject", "")

                    # Check sender
                    if sender_contains.lower() not in sender.lower():
                        continue

                    # Check subject if specified
                    if subject_contains and subject_contains.lower() not in subject.lower():
                        continue

                    # Extract OTP from email body
                    body_content = email.get("body", {}).get("content", "")
                    body_type = email.get("body", {}).get("contentType", "text")

                    # Convert HTML to text if needed
                    if body_type.lower() == "html":
                        body_content = self._html_to_text(body_content)

                    otp = self._extract_otp(body_content, otp_pattern)
                    if otp:
                        logger.info(f"Found OTP code: {otp}")
                        return otp

                logger.debug(f"No OTP email found yet, waiting {poll_interval}s...")

            except httpx.HTTPStatusError as e:
                logger.error(f"Graph API error: {e.response.status_code} - {e.response.text}")
            except Exception as e:
                logger.warning(f"Error checking email: {e}")

            await asyncio.sleep(poll_interval)

        logger.error(f"Timeout ({timeout_seconds}s) waiting for OTP email")
        return None

    def _html_to_text(self, html: str) -> str:
        """Convert HTML email body to plain text."""
        try:
            soup = BeautifulSoup(html, "html.parser")
            # Remove script and style elements
            for element in soup(["script", "style"]):
                element.decompose()
            text = soup.get_text(separator=" ")
            # Clean up whitespace
            text = re.sub(r'\s+', ' ', text)
            return unescape(text)
        except Exception:
            # Fallback: simple tag stripping
            text = re.sub(r'<[^>]+>', ' ', html)
            return unescape(text)

    def _extract_otp(self, email_body: str, custom_pattern: str | None = None) -> str | None:
        """
        Extract OTP code from email body.

        Args:
            email_body: Plain text email content
            custom_pattern: Custom regex pattern (optional)

        Returns:
            Extracted OTP code or None
        """
        if custom_pattern:
            match = re.search(custom_pattern, email_body, re.IGNORECASE)
            if match:
                return match.group(1)

        # Common OTP patterns (ordered by specificity)
        patterns = [
            r'(?:code|otp|pin)[:\s]+(\d{6})',      # "code: 123456" or "OTP 123456"
            r'(?:code|otp|pin)[:\s]+(\d{4})',      # "code: 1234"
            r'(?:verification|confirm)[:\s]+(\d{6})',  # "verification: 123456"
            r'\b(\d{6})\b',                         # Any 6 digit number
            r'\b(\d{8})\b',                         # Any 8 digit number
            r'\b(\d{4})\b',                         # Any 4 digit number (last resort)
        ]

        for pattern in patterns:
            match = re.search(pattern, email_body, re.IGNORECASE)
            if match:
                return match.group(1)

        return None

    async def get_recent_emails(self, count: int = 10) -> list[dict]:
        """Get recent emails for debugging purposes."""
        token = await self._get_token()

        url = f"{self.GRAPH_URL}/users/{self.user_email}/messages"
        params = {
            "$orderby": "receivedDateTime desc",
            "$top": count,
            "$select": "subject,from,receivedDateTime"
        }

        async with httpx.AsyncClient() as client:
            response = await client.get(
                url,
                params=params,
                headers={"Authorization": f"Bearer {token}"}
            )
            response.raise_for_status()
            return response.json().get("value", [])
