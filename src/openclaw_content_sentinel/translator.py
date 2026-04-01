from __future__ import annotations

import logging
from abc import ABC, abstractmethod

from .config import AppConfig
from .models import PreferredLanguage

logger = logging.getLogger(__name__)


class BaseTranslator(ABC):
    """Ref §7.2: Abstract base for translation adapters."""

    @abstractmethod
    def translate(self, text: str, target_lang: PreferredLanguage | str) -> str:
        pass


class LLMTranslator(BaseTranslator):
    """Ref §7.3: High-quality translation via LLM."""

    def __init__(self, config: AppConfig) -> None:
        self.config = config

    def translate(self, text: str, target_lang: PreferredLanguage | str) -> str:
        if not text:
            return ""

        lang_str = (
            target_lang.value if isinstance(target_lang, PreferredLanguage) else str(target_lang)
        )

        logger.info(f"Translating content to {lang_str} via LLM...")
        # In a real implementation, this would call the OpenClaw LLM gateway
        # For the sprint, we simulate the 'gold' translation logic
        return f"[Translated to {lang_str}] {text}"


class GoogleTranslator(BaseTranslator):
    """Ref §7.4: Fast fallback translation."""

    def translate(self, text: str, target_lang: PreferredLanguage | str) -> str:
        lang_str = (
            target_lang.value if isinstance(target_lang, PreferredLanguage) else str(target_lang)
        )
        logger.info(f"Fallback translation to {lang_str} via Google API...")
        return f"[Google-fallback to {lang_str}] {text}"


def get_translator(config: AppConfig, kind: str = "llm") -> BaseTranslator:
    if kind == "google":
        return GoogleTranslator()
    return LLMTranslator(config)
