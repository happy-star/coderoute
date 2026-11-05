"""The OpenAI SDK is aimed at Otari and sends the code-execution tool."""

import json

import httpx
import pytest

from coderoute.otari_client import CODE_EXECUTION_TOOL, OtariClient, OtariError


def _gateway(handler):
    return httpx.Client(transport=httpx.MockTransport(handler))


def _completion(content="print(1)", usage=None, tool_calls=None):
    body = {
        "id": "chatcmpl_test",
        "object": "chat.completion",
        "model": "openai/gpt-oss-20b",
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": content},
                "finish_reason": "stop",
            }
        ],
        "usage": usage
        or {
            "prompt_tokens": 10,
            "completion_tokens": 4,
            "total_tokens": 14,
            "cost_usd": "0.000021",
            "pricing_source": "defaults",
        },
    }
    if tool_calls is not None:
        body["otari_tool_calls"] = tool_calls
    return body


def test_chat_sends_otari_code_execution_tool_and_reads_cost():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["auth"] = request.headers.get("authorization")
        seen["otari_key"] = request.headers.get("otari-key")
        seen["executor"] = request.headers.get("otari-code-execution")
        seen["body"] = json.loads(request.content.decode())
        return httpx.Response(
            200,
            json=_completion(
                tool_calls=[
                    {
                        "id": "otari_ci_1",
                        "type": "code_execution",
                        "status": "completed",
                        "code": "print(1)",
                        "stdout": "1\n",
                        "stderr": "",
                        "exit_code": 0,
                    }
                ]
            ),
            headers={
                "Otari-Request-ID": "req_coder",
                "Otari-Model": "openai/gpt-oss-20b",
                "Otari-Provider": "groq",
            },
        )

    client = OtariClient(
        api_key="otk_test",
        base_url="https://api.otari.ai/api/v1",
        auth="bearer",
        http_client=_gateway(handler),
    )
    result = client.complete(
        model="openai/gpt-oss-20b",
        messages=[{"role": "user", "content": "print 1"}],
        pass_name="coder",
        temperature=0.2,
        max_tokens=200,
        code_execution=True,
    )

    assert seen["url"] == "https://api.otari.ai/api/v1/chat/completions"
    assert seen["auth"] == "Bearer otk_test"
    assert seen["otari_key"] is None
    assert seen["executor"] == "otari"
    assert seen["body"]["tools"] == [CODE_EXECUTION_TOOL]
    assert seen["body"]["metadata"] == {"app": "coderoute", "pass": "coder"}
    assert seen["body"]["max_tool_iterations"] == 3
    assert result["usage"]["cost_usd"] == "0.000021"
    assert result["usage"]["pricing_source"] == "defaults"
    assert result["request_id"] == "req_coder"
    assert result["provider"] == "groq"
    assert result["tool_calls"][0]["stdout"] == "1\n"


def test_otari_key_header_replaces_bearer():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["auth"] = request.headers.get("authorization")
        seen["otari_key"] = request.headers.get("otari-key")
        return httpx.Response(
            200,
            json={
                "object": "code_execution.result",
                "stdout": "2\n",
                "stderr": "",
                "return_code": 0,
                "files": [],
            },
            headers={"Otari-Request-ID": "req_exec"},
        )

    client = OtariClient(
        api_key="otk_test",
        base_url="https://api.otari.ai/api/v1/",
        auth="otari-key",
        http_client=_gateway(handler),
    )
    result = client.execute_code("print(1 + 1)")

    assert seen["otari_key"] == "otk_test"
    assert seen["auth"] is None
    assert result["stdout"] == "2\n"
    assert result["return_code"] == 0
    assert result["request_id"] == "req_exec"


def test_budget_exceeded_is_structured():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            403,
            json={"detail": "user has exceeded budget limit", "code": "budget_exceeded"},
            headers={"Otari-Budget-Scope": "user", "Retry-After": "3600", "Otari-Request-ID": "req_no"},
        )

    client = OtariClient(
        api_key="otk_test",
        base_url="https://api.otari.ai/api/v1",
        http_client=_gateway(handler),
    )
    with pytest.raises(OtariError) as caught:
        client.complete(
            model="openai/gpt-oss-20b",
            messages=[{"role": "user", "content": "hi"}],
            pass_name="coder",
            temperature=0,
            max_tokens=16,
        )
    err = caught.value
    assert err.status == 403
    assert err.code == "budget_exceeded"
    assert err.to_dict()["budget_scope"] == "user"
    assert err.to_dict()["retry_after"] == "3600"


def test_list_models_and_budgets_ignore_forbidden_list():
    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path.endswith("/models"):
            return httpx.Response(
                200,
                json={
                    "object": "list",
                    "data": [
                        {
                            "id": "openai/gpt-oss-20b",
                            "object": "model",
                            "created": 0,
                            "owned_by": "openai",
                            "type": "chat",
                            "pricing": {
                                "input_price_per_million": 0.1,
                                "output_price_per_million": 0.4,
                            },
                        }
                    ],
                },
            )
        if path.endswith("/organizations/me/budgets"):
            return httpx.Response(
                200,
                json={
                    "data": [
                        {
                            "budget_id": "bdg_1",
                            "name": "workshop",
                            "max_budget": 5,
                            "token_limit": None,
                            "request_limit": 100,
                        }
                    ],
                    "count": 1,
                },
            )
        if path.endswith("/budgets"):
            return httpx.Response(
                403,
                json={"detail": "master key required", "code": "forbidden"},
            )
        if path.endswith("/usage/summary"):
            return httpx.Response(
                200,
                json={
                    "start_date": "2026-10-06",
                    "end_date": "2026-11-05",
                    "totals": {"cost": 0.02, "total_tokens": 40, "request_count": 2, "error_count": 0},
                },
            )
        return httpx.Response(404, json={"detail": path})

    client = OtariClient(
        api_key="otk_test",
        base_url="https://api.otari.ai/api/v1",
        http_client=_gateway(handler),
    )
    models = client.list_models()
    assert models[0]["id"] == "openai/gpt-oss-20b"
    assert models[0]["pricing"]["output_price_per_million"] == 0.4
    summary = client.usage_summary()
    assert summary["cost"] == 0.02
    budgets = client.list_budgets()
    assert budgets["sources"][0]["budgets"][0]["name"] == "workshop"
    assert budgets["sources"][1]["error"] == "forbidden"
