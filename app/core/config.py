"""
Configuration module — reads all settings from environment variables.
No secrets are hardcoded here.
"""

import os
from functools import lru_cache


class Settings:
    # Team / submission identity
    TEAM_NAME: str = os.getenv("TEAM_NAME", "Team Vera")
    TEAM_MEMBERS: str = os.getenv("TEAM_MEMBERS", "Member1")
    CONTACT_EMAIL: str = os.getenv("CONTACT_EMAIL", "contact@example.com")
    APP_VERSION: str = os.getenv("APP_VERSION", "1.0.0")
    SUBMITTED_AT: str = os.getenv("SUBMITTED_AT", "2026-09-27T00:00:00Z")

    # LLM provider configuration
    LLM_PROVIDER: str = os.getenv("LLM_PROVIDER", "gemini")  # gemini, openai, anthropic, deepseek
    LLM_API_KEY: str = os.getenv("LLM_API_KEY", "")
    LLM_MODEL: str = os.getenv("LLM_MODEL", "")  # empty = use provider default
    LLM_TIMEOUT: int = int(os.getenv("LLM_TIMEOUT", "20"))  # seconds

    # Model name to advertise in /v1/metadata
    MODEL_NAME: str = os.getenv("MODEL_NAME", "gemini-3.8-flash")

    # Server
    PORT: int = int(os.getenv("PORT", "8080"))
    HOST: str = os.getenv("HOST", "0.0.0.0")

    # Auto-reply detection thresholds
    AUTO_REPLY_IDENTICAL_THRESHOLD: int = 2   # N identical messages → switch to wait
    AUTO_REPLY_MAX_TURNS_BEFORE_END: int = 3  # After this many auto-replies → end

    # Conversation suppression
    SUPPRESSION_SET: set = set()  # holds suppression_keys already sent


@lru_cache()
def get_settings() -> Settings:
    return Settings()
