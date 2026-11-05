"""CodeRoute web app."""

from __future__ import annotations

import os
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from coderoute.config import (
    OTARI_DOCS_BUDGETS,
    OTARI_DOCS_CODE_EXECUTE,
    OTARI_DOCS_CODE_SETTINGS,
    OTARI_DOCS_QUICKSTART,
    OTARI_DOCS_TOOLS,
    OTARI_GITHUB,
    OTARI_HOME,
    load_settings,
)
from coderoute.otari_client import OtariClient, OtariError
from coderoute.pipeline import dashboard_links, mock_result, run

STATIC = Path(__file__).resolve().parent.parent / "static"
app = FastAPI(title="CodeRoute", version="0.1.0")


class GenerateIn(BaseModel):
    task: str = Field(min_length=1, max_length=4000)
    coder_model: str | None = None
    review_model: str | None = None
    mock: bool | None = None


def _client(settings) -> OtariClient:
    return OtariClient(
        api_key=settings.api_key,
        base_url=settings.base_url,
        auth=settings.auth,
        timeout=settings.timeout_seconds,
    )


def _public_config(settings) -> dict:
    links = dashboard_links(settings.dashboard_url)
    return {
        "mode": "live" if settings.live else "mock",
        "base_url": settings.base_url,
        "auth": settings.auth,
        "coder_model": settings.coder_model,
        "review_model": settings.review_model,
        "review_fallback": settings.review_fallback,
        "has_api_key": bool(settings.api_key),
        "links": {
            **links,
            "otari": OTARI_HOME,
            "github": OTARI_GITHUB,
            "quickstart": OTARI_DOCS_QUICKSTART,
            "docs_tools": OTARI_DOCS_TOOLS,
            "code_execute": OTARI_DOCS_CODE_EXECUTE,
            "code_settings": OTARI_DOCS_CODE_SETTINGS,
            "budgets_docs": OTARI_DOCS_BUDGETS,
        },
    }


@app.get("/api/health")
def health():
    settings = load_settings()
    return {"ok": True, "mode": "live" if settings.live else "mock", "service": "coderoute"}


@app.get("/api/config")
def config():
    return _public_config(load_settings())


@app.get("/api/models")
def models():
    settings = load_settings()
    if not settings.live:
        return {"mode": "mock", "models": []}
    try:
        return {"mode": "live", "models": _client(settings).list_models()}
    except OtariError as exc:
        return JSONResponse({"mode": "live", "models": [], **exc.to_dict()}, status_code=exc.status)


@app.post("/api/generate")
def generate(body: GenerateIn):
    settings = load_settings()
    task = body.task.strip()
    if not task:
        return JSONResponse({"error": "empty_task", "detail": "Enter a task."}, status_code=400)
    coder_model = (body.coder_model or "").strip() or settings.coder_model
    review_model = (body.review_model or "").strip() or settings.review_model
    use_mock = body.mock if body.mock is not None else not settings.live
    if use_mock is False and not settings.live:
        return JSONResponse(
            {
                "error": "missing_api_key",
                "detail": "Set OTARI_API_KEY to call Otari, or leave mock mode on.",
            },
            status_code=400,
        )
    if use_mock or not settings.live:
        return mock_result(task, settings, coder_model=coder_model, review_model=review_model)
    try:
        return run(
            task,
            settings,
            _client(settings),
            coder_model=coder_model,
            review_model=review_model,
        )
    except OtariError as exc:
        status = exc.status if 400 <= exc.status < 600 else 502
        return JSONResponse(exc.to_dict(), status_code=status)


@app.get("/")
def index():
    return FileResponse(STATIC / "index.html")


app.mount("/static", StaticFiles(directory=STATIC), name="static")


def main() -> None:
    import uvicorn

    uvicorn.run(
        "coderoute.app:app",
        host="0.0.0.0",
        port=int(os.environ.get("PORT", "8000")),
    )


if __name__ == "__main__":
    main()
