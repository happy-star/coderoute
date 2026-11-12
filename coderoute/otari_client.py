"""Otari gateway calls through the official OpenAI SDK.

The SDK ``base_url`` is the gateway root, ``https://api.otari.ai/api/v1``.
Chat completions stay on the typed helper so usage fields (including
``usage.cost_usd`` and ``usage.pricing_source``) come back on the completion.
Otari-only fields travel in ``extra_body`` / ``extra_headers``:

- ``tools: [{"type": "otari_code_execution"}]`` asks the gateway to run code
  in its sandbox during the completion (docs: Built-in tools).
- ``Otari-Code-Execution: otari`` pins that sandbox when a request also uses
  a provider-native code tool name.
- ``POST /code/execute`` runs Python the caller already has, with no model.
- ``GET /models``, ``GET /usage/summary``, ``GET /budgets``, and
  ``GET /organizations/me/budgets`` are the same routes the dashboard uses.

Paths are relative to ``base_url`` and match the hosted OpenAPI (gateway 0.38).
"""

from __future__ import annotations

import threading
from typing import Any

import httpx
from openai import APIStatusError, OpenAI

CODE_EXECUTION_TOOL: dict[str, str] = {"type": "otari_code_execution"}

_HEADER_KEYS = (
    "otari-request-id",
    "otari-model",
    "otari-provider",
    "otari-attempt-count",
    "otari-fallback",
    "otari-response-duration-ms",
    "otari-container-id",
    "otari-container-expires-at",
    "otari-error-code",
    "otari-budget-scope",
    "retry-after",
)


class OtariError(Exception):
    def __init__(
        self,
        status: int,
        detail: str,
        *,
        code: str | None = None,
        headers: dict[str, str] | None = None,
    ) -> None:
        super().__init__(detail)
        self.status = status
        self.detail = detail
        self.code = code
        self.headers = headers or {}

    def to_dict(self) -> dict[str, Any]:
        return {
            "error": self.code or "otari_error",
            "status": self.status,
            "detail": self.detail,
            "code": self.code,
            "budget_scope": self.headers.get("otari-budget-scope"),
            "retry_after": self.headers.get("retry-after"),
            "request_id": self.headers.get("otari-request-id"),
        }


def _as_dict(obj: Any) -> dict[str, Any]:
    if obj is None:
        return {}
    if isinstance(obj, dict):
        return dict(obj)
    data: dict[str, Any] = {}
    if hasattr(obj, "model_dump"):
        dumped = obj.model_dump()
        if isinstance(dumped, dict):
            data.update(dumped)
    extra = getattr(obj, "model_extra", None) or {}
    if isinstance(extra, dict):
        data.update(extra)
    return data


def usage_view(usage: Any) -> dict[str, Any] | None:
    data = _as_dict(usage)
    if not data:
        return None
    kept = {}
    for key in (
        "prompt_tokens",
        "completion_tokens",
        "total_tokens",
        "cost_usd",
        "pricing_source",
    ):
        if data.get(key) is not None:
            kept[key] = data[key]
    return kept or None


def response_headers(headers: Any) -> dict[str, str]:
    found: dict[str, str] = {}
    for key in _HEADER_KEYS:
        value = headers.get(key) if hasattr(headers, "get") else None
        if value:
            found[key] = str(value)
    return found


def _error_from_status(exc: APIStatusError) -> OtariError:
    body = exc.body
    detail = str(exc)
    code = None
    if isinstance(body, dict):
        raw_detail = body.get("detail", body.get("message", body))
        detail = raw_detail if isinstance(raw_detail, str) else str(raw_detail)
        code = body.get("code")
        if isinstance(code, dict):
            code = code.get("code")
    elif isinstance(body, str) and body:
        detail = body
    headers = response_headers(exc.response.headers)
    if not code:
        code = headers.get("otari-error-code")
    return OtariError(exc.status_code, detail, code=code, headers=headers)


class OtariClient:
    """Thin wrapper. ``http_client`` is injectable so tests can mock transport."""

    def __init__(
        self,
        *,
        api_key: str,
        base_url: str,
        auth: str = "bearer",
        timeout: float = 120.0,
        http_client: httpx.Client | None = None,
    ) -> None:
        if auth not in {"bearer", "otari-key"}:
            raise ValueError("auth must be 'bearer' or 'otari-key'")
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.auth = auth
        self._local = threading.local()

        def remember(response: httpx.Response) -> None:
            self._local.headers = response_headers(response.headers)

        hooks: dict[str, list] = {"response": [remember]}
        if auth == "otari-key":

            def use_otari_key(request: httpx.Request) -> None:
                request.headers["Otari-Key"] = api_key
                request.headers.pop("Authorization", None)

            hooks["request"] = [use_otari_key]

        if http_client is None:
            http_client = httpx.Client(timeout=timeout, event_hooks=hooks)
        else:
            http_client.event_hooks.setdefault("response", []).append(remember)
            if auth == "otari-key":
                http_client.event_hooks.setdefault("request", []).extend(hooks["request"])

        self._http = http_client
        self._sdk = OpenAI(
            api_key=api_key,
            base_url=self.base_url,
            timeout=timeout,
            max_retries=0,
            http_client=http_client,
        )

    def _call(self, fn):
        try:
            return fn()
        except APIStatusError as exc:
            raise _error_from_status(exc) from exc

    def _last_headers(self) -> dict[str, str]:
        return dict(getattr(self._local, "headers", {}) or {})

    def complete(
        self,
        *,
        model: str,
        messages: list[dict[str, str]],
        pass_name: str,
        temperature: float,
        max_tokens: int,
        code_execution: bool = False,
    ) -> dict[str, Any]:
        extra_body: dict[str, Any] = {
            "metadata": {"app": "coderoute", "pass": pass_name},
        }
        extra_headers: dict[str, str] = {}
        if code_execution:
            extra_body["tools"] = [dict(CODE_EXECUTION_TOOL)]
            extra_body["max_tool_iterations"] = 3
            extra_headers["Otari-Code-Execution"] = "otari"

        def send():
            raw = self._sdk.chat.completions.with_raw_response.create(
                model=model,
                messages=messages,
                temperature=temperature,
                max_tokens=max_tokens,
                extra_body=extra_body,
                extra_headers=extra_headers or None,
            )
            completion = raw.parse()
            headers = response_headers(raw.headers)
            return completion, headers

        completion, headers = self._call(send)
        choice = completion.choices[0]
        message = choice.message
        content = message.content or ""
        tool_calls = _as_dict(completion).get("otari_tool_calls") or []
        if not isinstance(tool_calls, list):
            tool_calls = []
        return {
            "model": model,
            "content": content,
            "finish_reason": choice.finish_reason,
            "tool_calls": tool_calls,
            "usage": usage_view(completion.usage),
            "headers": headers,
            "request_id": headers.get("otari-request-id"),
            "provider": headers.get("otari-provider"),
            "served_model": headers.get("otari-model"),
        }

    def execute_code(self, code: str, *, timeout_seconds: int = 20) -> dict[str, Any]:
        body = {"code": code, "timeout_seconds": timeout_seconds}

        def send():
            return self._sdk.post("/code/execute", body=body, cast_to=object)

        payload = self._call(send)
        if not isinstance(payload, dict):
            payload = _as_dict(payload)
        headers = self._last_headers()
        return {
            "stdout": payload.get("stdout") or "",
            "stderr": payload.get("stderr") or "",
            "return_code": payload.get("return_code"),
            "files": payload.get("files") or [],
            "object": payload.get("object"),
            "headers": headers,
            "request_id": headers.get("otari-request-id"),
        }

    def list_models(self) -> list[dict[str, Any]]:
        payload = self._call(lambda: self._sdk.get("/models", cast_to=object))
        rows = payload.get("data", []) if isinstance(payload, dict) else []
        models = []
        for row in rows:
            if not isinstance(row, dict) or not row.get("id"):
                continue
            models.append(
                {
                    "id": row["id"],
                    "type": row.get("type"),
                    "owned_by": row.get("owned_by"),
                    "pricing": row.get("pricing"),
                    "pricing_source": row.get("pricing_source"),
                }
            )
        return models

    def usage_summary(self) -> dict[str, Any]:
        payload = self._call(lambda: self._sdk.get("/usage/summary", cast_to=object))
        if not isinstance(payload, dict):
            return {}
        totals = payload.get("totals") or {}
        return {
            "start_date": payload.get("start_date"),
            "end_date": payload.get("end_date"),
            "cost": totals.get("cost"),
            "total_tokens": totals.get("total_tokens"),
            "prompt_tokens": totals.get("prompt_tokens"),
            "completion_tokens": totals.get("completion_tokens"),
            "request_count": totals.get("request_count"),
            "error_count": totals.get("error_count"),
        }

    def list_budgets(self) -> dict[str, Any]:
        """Read budgets the key is allowed to see. A 403 is reported, not raised."""
        sources: list[dict[str, Any]] = []
        for path, kind in (
            ("/organizations/me/budgets", "organization"),
            ("/budgets", "deployment"),
        ):
            try:
                payload = self._call(lambda path=path: self._sdk.get(path, cast_to=object))
            except OtariError as exc:
                sources.append(
                    {
                        "source": kind,
                        "error": exc.code or "otari_error",
                        "status": exc.status,
                        "detail": exc.detail,
                        "budgets": [],
                    }
                )
                continue
            sources.append({"source": kind, "budgets": _budget_rows(payload), "error": None})
        return {"sources": sources}


def _budget_rows(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        rows = payload
    elif isinstance(payload, dict):
        rows = payload.get("data") or []
    else:
        rows = []
    cleaned = []
    for row in rows[:12]:
        if not isinstance(row, dict):
            continue
        cleaned.append(
            {
                "budget_id": row.get("budget_id"),
                "name": row.get("name"),
                "max_budget": row.get("max_budget"),
                "token_limit": row.get("token_limit"),
                "request_limit": row.get("request_limit"),
                "total_spend": row.get("total_spend"),
                "total_reserved": row.get("total_reserved"),
            }
        )
    return cleaned
