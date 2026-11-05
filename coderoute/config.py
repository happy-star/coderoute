"""Environment for the Otari hosted gateway.

Defaults follow the current hosted docs (https://docs.otari.ai/getting-started/quickstart):
base URL ``https://api.otari.ai/api/v1`` and ``Authorization: Bearer``. The live
gateway also accepts the OpenAPI ``Otari-Key`` scheme; set ``OTARI_AUTH=otari-key``.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

from dotenv import load_dotenv

DEFAULT_BASE_URL = "https://api.otari.ai/api/v1"
DEFAULT_DASHBOARD_URL = "https://otari.ai/dashboard"
DEFAULT_CODER_MODEL = "openai/gpt-oss-20b"
DEFAULT_REVIEW_MODEL = "auto"
DEFAULT_REVIEW_FALLBACK = "openai/gpt-oss-safeguard-20b"

OTARI_HOME = "https://otari.ai"
OTARI_GITHUB = "https://github.com/mozilla-ai/otari"
OTARI_DOCS_QUICKSTART = "https://docs.otari.ai/getting-started/quickstart"
OTARI_DOCS_TOOLS = "https://docs.otari.ai/otari/for-integrators/tools"
OTARI_DOCS_CODE_EXECUTE = "https://docs.otari.ai/otari/for-integrators/api-reference/code-execution"
OTARI_DOCS_BUDGETS = "https://docs.otari.ai/learn/budgets"
OTARI_DOCS_CODE_SETTINGS = "https://docs.otari.ai/tools/configure-code-execution"


@dataclass(frozen=True)
class Settings:
    api_key: str
    base_url: str
    auth: str
    coder_model: str
    review_model: str
    review_fallback: str
    dashboard_url: str
    force_mock: bool
    timeout_seconds: float = 120.0

    @property
    def live(self) -> bool:
        return bool(self.api_key) and not self.force_mock


def load_settings() -> Settings:
    load_dotenv(override=False)
    auth = os.environ.get("OTARI_AUTH", "bearer").strip().lower()
    if auth not in {"bearer", "otari-key"}:
        auth = "bearer"
    base = os.environ.get("OTARI_BASE_URL", DEFAULT_BASE_URL).strip() or DEFAULT_BASE_URL
    dashboard = os.environ.get("OTARI_DASHBOARD_URL", DEFAULT_DASHBOARD_URL).strip()
    dashboard = dashboard or DEFAULT_DASHBOARD_URL
    return Settings(
        api_key=os.environ.get("OTARI_API_KEY", "").strip(),
        base_url=base.rstrip("/"),
        auth=auth,
        coder_model=os.environ.get("OTARI_CODER_MODEL", DEFAULT_CODER_MODEL).strip()
        or DEFAULT_CODER_MODEL,
        review_model=os.environ.get("OTARI_REVIEW_MODEL", DEFAULT_REVIEW_MODEL).strip()
        or DEFAULT_REVIEW_MODEL,
        review_fallback=os.environ.get("OTARI_REVIEW_FALLBACK", DEFAULT_REVIEW_FALLBACK).strip()
        or DEFAULT_REVIEW_FALLBACK,
        dashboard_url=dashboard.rstrip("/"),
        force_mock=os.environ.get("OTARI_MOCK", "").strip().lower() in {"1", "true", "yes"},
    )
