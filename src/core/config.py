from pydantic_settings import BaseSettings
from pydantic import Field
from typing import Literal
from functools import lru_cache


class Settings(BaseSettings):
    # Database
    database_url: str = Field(
        default=(
            "mssql+aioodbc:///?odbc_connect="
            "Driver%3D%7BODBC+Driver+18+for+SQL+Server%7D%3B"
            "Server%3Dtcp%3Alocalhost%2C1433%3B"
            "Database%3Dworkflow_db%3B"
            "Uid%3Dsa%3B"
            "Pwd%3DYourStrong!Passw0rd%3B"
            "Encrypt%3Dno%3B"
            "TrustServerCertificate%3Dyes%3B"
            "Connection+Timeout%3D30%3B"
        )
    )

    # LLM Provider
    llm_provider: Literal["gemini", "openai", "anthropic"] = Field(default="gemini")
    gemini_api_key: str = Field(default="")
    openai_api_key: str = Field(default="")
    anthropic_api_key: str = Field(default="")

    # LLM Model settings
    gemini_model: str = Field(default="gemini-3-flash-preview")
    openai_model: str = Field(default="gpt-4o")
    anthropic_model: str = Field(default="claude-sonnet-4-20250514")

    # App settings
    app_env: Literal["development", "staging", "production"] = Field(default="development")
    log_level: str = Field(default="INFO")

    # Browser settings
    browser_headless: bool = Field(default=True)
    browser_timeout: int = Field(default=30000)  # milliseconds

    # M365 / Azure AD (for OTP email retrieval)
    m365_tenant_id: str = Field(default="")
    m365_client_id: str = Field(default="")
    m365_client_secret: str = Field(default="")
    m365_user_email: str = Field(default="")

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
