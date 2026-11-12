"""Coder pass, review pass, then Otari sandbox execution."""

from __future__ import annotations

import json
import re
from decimal import Decimal, InvalidOperation
from typing import Any
from urllib.parse import quote

from coderoute.config import Settings
from coderoute.otari_client import OtariClient, OtariError

_FENCE = re.compile(r"```(?:python|py)?\s*\n(.*?)```", re.DOTALL | re.IGNORECASE)
_JSON_FENCE = re.compile(r"```(?:json)?\s*\n?(.*?)```", re.DOTALL | re.IGNORECASE)

CODER_SYSTEM = """You are CodeRoute's coder, called through the Otari gateway.
Turn the task into one Python 3 program that uses only the standard library.
Print the result to stdout.
Do not use the network, subprocess, or files outside the working directory.
If a code execution tool is available, run the program once, fix it if it fails, and stop.
End your reply with one fenced python block that is the full program. Do not write anything after that fence."""

REVIEW_SYSTEM = """You are CodeRoute's reviewer, a second model on the Otari gateway.
You receive a task and a Python program. Reply with one JSON object and no markdown:
{"verdict":"approve","code":"","notes":"one sentence"}
or
{"verdict":"revise","code":"<full corrected program>","notes":"one sentence"}
verdict must be approve or revise.
Use revise when the program is wrong, empty, or is not standard-library Python that prints a result.
Use approve when it already does the task. notes is one sentence."""


def extract_python(text: str) -> str | None:
    if not text or not text.strip():
        return None
    fenced = _FENCE.findall(text)
    if fenced:
        code = fenced[-1].strip()
        return code or None
    stripped = text.strip()
    first = stripped.splitlines()[0]
    if first.startswith(("print(", "def ", "import ", "from ", "class ", "#")):
        return stripped
    return None


def parse_review(text: str) -> dict[str, Any]:
    raw = (text or "").strip()
    fenced = _JSON_FENCE.search(raw)
    if fenced:
        raw = fenced.group(1).strip()
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return {
            "verdict": "approve",
            "code": "",
            "notes": (text or "").strip()[:500],
            "parsed": False,
        }
    if not isinstance(data, dict):
        return {"verdict": "approve", "code": "", "notes": (text or "").strip()[:500], "parsed": False}
    verdict = str(data.get("verdict") or "approve").lower()
    if verdict not in {"approve", "revise"}:
        verdict = "approve"
    code = data.get("code") or ""
    notes = data.get("notes") or ""
    return {
        "verdict": verdict,
        "code": code.strip() if isinstance(code, str) else "",
        "notes": notes.strip() if isinstance(notes, str) else str(notes),
        "parsed": True,
    }


def choose_review_model(
    coder_model: str,
    requested: str,
    models: list[dict[str, Any]] | None,
    fallback: str,
) -> tuple[str, str]:
    """Pick the review model. ``auto`` prefers the cheapest other chat model."""
    if requested and requested != "auto":
        return requested, "configured"
    catalog = models or []
    priced: list[tuple[float, float, str]] = []
    unpriced: list[str] = []
    for row in catalog:
        model_id = row.get("id")
        if not model_id or model_id == coder_model:
            continue
        if row.get("type") not in (None, "chat"):
            continue
        pricing = row.get("pricing") or {}
        output_price = pricing.get("output_price_per_million")
        input_price = pricing.get("input_price_per_million")
        if isinstance(output_price, (int, float)) and isinstance(input_price, (int, float)):
            priced.append((float(output_price), float(input_price), model_id))
        else:
            unpriced.append(model_id)
    if priced:
        priced.sort()
        return priced[0][2], "cheapest_other_chat_model"
    if fallback and fallback != coder_model:
        return fallback, "fallback"
    if unpriced:
        return unpriced[0], "first_other_chat_model"
    return fallback or coder_model, "fallback"


def sum_usage(usages: list[dict[str, Any] | None]) -> dict[str, Any]:
    total_tokens = 0
    prompt = 0
    completion = 0
    costs: list[Decimal] = []
    sources = []
    for usage in usages:
        if not usage:
            continue
        total_tokens += int(usage.get("total_tokens") or 0)
        prompt += int(usage.get("prompt_tokens") or 0)
        completion += int(usage.get("completion_tokens") or 0)
        if usage.get("cost_usd") is not None:
            try:
                costs.append(Decimal(str(usage["cost_usd"])))
            except (InvalidOperation, ValueError):
                pass
        if usage.get("pricing_source"):
            sources.append(usage["pricing_source"])
    cost = f"{sum(costs, Decimal('0')):.6f}" if costs else None
    return {
        "prompt_tokens": prompt,
        "completion_tokens": completion,
        "total_tokens": total_tokens,
        "cost_usd": cost,
        "pricing_sources": sources,
    }


def dashboard_links(dashboard_url: str, request_id: str | None = None) -> dict[str, str]:
    base = dashboard_url.rstrip("/")
    links = {
        "activity": f"{base}/requests",
        "usage": f"{base}/",
        "budgets": f"{base}/budgets",
        "tools": f"{base}/tools",
    }
    if request_id:
        links["activity_request"] = f"{base}/requests?request={quote(request_id, safe='')}"
    return links


def _code_from_tool_calls(tool_calls: list[dict[str, Any]]) -> str | None:
    for call in reversed(tool_calls):
        if not isinstance(call, dict):
            continue
        if call.get("type") not in (None, "code_execution"):
            continue
        code = call.get("code")
        if isinstance(code, str) and code.strip():
            return code.strip()
    return None


def run(task: str, settings: Settings, client: OtariClient, *, coder_model: str, review_model: str) -> dict[str, Any]:
    catalog = None
    catalog_error = None
    if review_model == "auto":
        try:
            catalog = client.list_models()
        except OtariError as exc:
            catalog_error = exc.to_dict()
    chosen_review, review_reason = choose_review_model(
        coder_model, review_model, catalog, settings.review_fallback
    )

    coder = client.complete(
        model=coder_model,
        messages=[
            {"role": "system", "content": CODER_SYSTEM},
            {"role": "user", "content": task},
        ],
        pass_name="coder",
        temperature=0.2,
        max_tokens=1600,
        code_execution=True,
    )
    code = extract_python(coder["content"]) or _code_from_tool_calls(coder["tool_calls"])
    if not code:
        raise OtariError(
            502,
            "The coder did not return a Python program.",
            code="coder_returned_no_code",
            headers=coder["headers"],
        )

    review_pass: dict[str, Any]
    try:
        reviewed = client.complete(
            model=chosen_review,
            messages=[
                {"role": "system", "content": REVIEW_SYSTEM},
                {
                    "role": "user",
                    "content": f"Task:\n{task}\n\nProgram:\n```python\n{code}\n```",
                },
            ],
            pass_name="review",
            temperature=0,
            max_tokens=700,
            code_execution=False,
        )
        parsed = parse_review(reviewed["content"])
        if parsed["verdict"] == "revise" and parsed["code"]:
            code = parsed["code"]
        review_pass = {
            "model": chosen_review,
            "selection": review_reason,
            "verdict": parsed["verdict"],
            "notes": parsed["notes"],
            "parsed": parsed["parsed"],
            "usage": reviewed["usage"],
            "request_id": reviewed["request_id"],
            "provider": reviewed["provider"],
            "served_model": reviewed["served_model"],
            "headers": reviewed["headers"],
            "error": None,
        }
    except OtariError as exc:
        review_pass = {
            "model": chosen_review,
            "selection": review_reason,
            "verdict": None,
            "notes": "",
            "parsed": False,
            "usage": None,
            "request_id": exc.headers.get("otari-request-id"),
            "provider": None,
            "served_model": None,
            "headers": exc.headers,
            "error": exc.to_dict(),
        }

    execution: dict[str, Any]
    try:
        ran = client.execute_code(code, timeout_seconds=20)
        execution = {
            "source": "otari_code_execute",
            "stdout": ran["stdout"],
            "stderr": ran["stderr"],
            "return_code": ran["return_code"],
            "files": ran["files"],
            "request_id": ran["request_id"],
            "error": None,
        }
    except OtariError as exc:
        execution = {
            "source": "otari_code_execute",
            "stdout": "",
            "stderr": "",
            "return_code": None,
            "files": [],
            "request_id": exc.headers.get("otari-request-id"),
            "error": exc.to_dict(),
        }

    usage_window = None
    budgets = None
    try:
        usage_window = client.usage_summary()
    except OtariError as exc:
        usage_window = {"error": exc.to_dict()}
    try:
        budgets = client.list_budgets()
    except OtariError as exc:
        budgets = {"error": exc.to_dict(), "sources": []}

    primary_request = coder["request_id"] or review_pass.get("request_id")
    return {
        "mode": "live",
        "task": task,
        "code": code,
        "coder": {
            "model": coder_model,
            "content_preview": coder["content"][:500],
            "tool_calls": coder["tool_calls"],
            "usage": coder["usage"],
            "request_id": coder["request_id"],
            "provider": coder["provider"],
            "served_model": coder["served_model"],
            "headers": coder["headers"],
        },
        "review": review_pass,
        "execution": execution,
        "usage": sum_usage([coder["usage"], review_pass.get("usage")]),
        "usage_window": usage_window,
        "budgets": budgets,
        "catalog_error": catalog_error,
        "links": dashboard_links(settings.dashboard_url, primary_request),
    }


def mock_result(task: str, settings: Settings, *, coder_model: str, review_model: str) -> dict[str, Any]:
    shown = task.strip()
    if len(shown) > 240:
        shown = shown[:240] + "…"
    code = (
        "print('CodeRoute mock mode')\n"
        "print('No request was sent to Otari.')\n"
        f"print({shown!r})\n"
    )
    stdout = f"CodeRoute mock mode\nNo request was sent to Otari.\n{shown}\n"
    return {
        "mode": "mock",
        "mock_notice": "Mock mode. Otari was not called. Code, stdout, and usage below are local stand-ins.",
        "task": task,
        "code": code,
        "coder": {
            "model": coder_model,
            "tool_calls": [],
            "usage": None,
            "request_id": None,
            "provider": None,
            "served_model": None,
            "headers": {},
        },
        "review": {
            "model": review_model if review_model != "auto" else settings.review_fallback,
            "selection": "mock",
            "verdict": "approve",
            "notes": "Mock reviewer. This is not an Otari model response.",
            "parsed": True,
            "usage": None,
            "request_id": None,
            "error": None,
        },
        "execution": {
            "source": "mock",
            "stdout": stdout,
            "stderr": "",
            "return_code": 0,
            "files": [],
            "request_id": None,
            "error": None,
        },
        "usage": {
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "total_tokens": 0,
            "cost_usd": None,
            "pricing_sources": [],
            "note": "Mock mode has no Otari usage or cost.",
        },
        "usage_window": None,
        "budgets": None,
        "catalog_error": None,
        "links": dashboard_links(settings.dashboard_url),
    }
