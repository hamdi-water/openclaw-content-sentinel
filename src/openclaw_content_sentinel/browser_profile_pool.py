from __future__ import annotations

import logging

from .config import AppConfig

logger = logging.getLogger(__name__)


class BrowserProfilePool:
    """Map a platform name to the configured persistent browser profile label."""

    def __init__(self, config: AppConfig) -> None:
        self.config = config
        self.profiles: dict[str, str] = {
            "linkedin": config.browser_profile_linkedin,
            "facebook": config.browser_profile_facebook,
            "x": config.browser_profile_x,
        }

    def get_profile(self, platform: str) -> str | None:
        """Return the configured profile label for one platform."""
        profile = self.profiles.get(platform.lower())
        if profile:
            return profile
        logger.warning("Profile for %s is not configured", platform)
        return None

    def list_available_platforms(self) -> list[str]:
        """List platforms that have a configured profile label."""
        return [platform for platform, profile in self.profiles.items() if profile]


def get_platform_profile(config: AppConfig, platform: str) -> str | None:
    """Return the configured persistent profile label for a platform."""
    return BrowserProfilePool(config).get_profile(platform)
