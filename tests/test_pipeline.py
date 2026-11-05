from coderoute.config import Settings
from coderoute.otari_client import OtariError
from coderoute.pipeline import choose_review_model, extract_python, mock_result, parse_review, run, sum_usage


def _settings() -> Settings:
    return Settings(
        api_key="otk_test",
        base_url="https://api.otari.ai/api/v1",
        auth="bearer",
        coder_model="openai/gpt-oss-20b",
        review_model="auto",
        review_fallback="openai/gpt-oss-safeguard-20b",
        dashboard_url="https://otari.ai/dashboard",
        force_mock=False,
    )


def test_extract_and_parse_review():
    assert extract_python("sure\n```python\nprint(1)\n```\n") == "print(1)"
    parsed = parse_review('```json\n{"verdict":"revise","code":"print(2)\\n","notes":"off by one"}\n```')
    assert parsed["verdict"] == "revise"
    assert parsed["code"] == "print(2)"
    assert parse_review("not json")["parsed"] is False


def test_auto_review_picks_cheapest_other_chat_model():
    models = [
        {
            "id": "openai/gpt-oss-20b",
            "type": "chat",
            "pricing": {"input_price_per_million": 0.1, "output_price_per_million": 0.5},
        },
        {
            "id": "embed/tiny",
            "type": "embedding",
            "pricing": {"input_price_per_million": 0.0001, "output_price_per_million": 0.0001},
        },
        {
            "id": "cheap/reviewer",
            "type": "chat",
            "pricing": {"input_price_per_million": 0.02, "output_price_per_million": 0.03},
        },
    ]
    chosen, reason = choose_review_model("openai/gpt-oss-20b", "auto", models, "openai/gpt-oss-safeguard-20b")
    assert chosen == "cheap/reviewer"
    assert reason == "cheapest_other_chat_model"
    pinned, reason = choose_review_model("openai/gpt-oss-20b", "openai/gpt-oss-safeguard-20b", models, "x")
    assert pinned == "openai/gpt-oss-safeguard-20b"
    assert reason == "configured"


def test_sum_usage_adds_reported_cost():
    total = sum_usage(
        [
            {"total_tokens": 10, "prompt_tokens": 6, "completion_tokens": 4, "cost_usd": "0.000100"},
            {"total_tokens": 5, "prompt_tokens": 3, "completion_tokens": 2, "cost_usd": "0.000020"},
        ]
    )
    assert total["total_tokens"] == 15
    assert total["cost_usd"] == "0.000120"


class FakeGateway:
    def __init__(self, review_body: str, execute_error: OtariError | None = None):
        self.review_body = review_body
        self.execute_error = execute_error
        self.executed = None
        self.coder_tools = None

    def list_models(self):
        return [
            {
                "id": "openai/gpt-oss-20b",
                "type": "chat",
                "pricing": {"input_price_per_million": 1, "output_price_per_million": 1},
            },
            {
                "id": "cheap/reviewer",
                "type": "chat",
                "pricing": {"input_price_per_million": 0.1, "output_price_per_million": 0.1},
            },
        ]

    def complete(self, **kwargs):
        if kwargs["pass_name"] == "coder":
            self.coder_tools = kwargs["code_execution"]
            return {
                "content": "```python\nprint(1 + 1)\n```",
                "tool_calls": [
                    {
                        "type": "code_execution",
                        "status": "completed",
                        "code": "print(1 + 1)",
                        "stdout": "2\n",
                        "exit_code": 0,
                    }
                ],
                "usage": {"prompt_tokens": 4, "completion_tokens": 4, "total_tokens": 8, "cost_usd": "0.000010", "pricing_source": "defaults"},
                "request_id": "req_coder",
                "provider": "groq",
                "served_model": kwargs["model"],
                "headers": {"otari-request-id": "req_coder"},
            }
        return {
            "content": self.review_body,
            "tool_calls": [],
            "usage": {"prompt_tokens": 2, "completion_tokens": 2, "total_tokens": 4, "cost_usd": "0.000002", "pricing_source": "defaults"},
            "request_id": "req_review",
            "provider": "together",
            "served_model": kwargs["model"],
            "headers": {},
        }

    def execute_code(self, code, timeout_seconds=20):
        self.executed = code
        if self.execute_error:
            raise self.execute_error
        return {"stdout": "2\n", "stderr": "", "return_code": 0, "files": [], "request_id": "req_exec"}

    def usage_summary(self):
        return {"cost": 0.25, "total_tokens": 12, "request_count": 3, "error_count": 0, "start_date": "2026-10-06", "end_date": "2026-11-05"}

    def list_budgets(self):
        return {
            "sources": [
                {
                    "source": "organization",
                    "error": None,
                    "budgets": [{"budget_id": "bdg_1", "name": "workshop", "max_budget": 5, "total_spend": 0.25}],
                }
            ]
        }


def test_pipeline_uses_two_models_and_executes_final_code():
    gateway = FakeGateway('{"verdict":"revise","code":"print(2)\\n","notes":"simpler"}')
    result = run("add one and one", _settings(), gateway, coder_model="openai/gpt-oss-20b", review_model="auto")
    assert gateway.coder_tools is True
    assert result["review"]["model"] == "cheap/reviewer"
    assert result["review"]["model"] != result["coder"]["model"]
    assert result["code"] == "print(2)"
    assert gateway.executed == "print(2)"
    assert result["execution"]["stdout"] == "2\n"
    assert result["execution"]["source"] == "otari_code_execute"
    assert result["coder"]["tool_calls"][0]["stdout"] == "2\n"
    assert result["usage"]["cost_usd"] == "0.000012"
    assert result["links"]["activity_request"] == "https://otari.ai/dashboard/requests?request=req_coder"
    assert result["links"]["budgets"] == "https://otari.ai/dashboard/budgets"
    assert result["budgets"]["sources"][0]["budgets"][0]["name"] == "workshop"
    assert result["usage_window"]["cost"] == 0.25


def test_sandbox_failure_still_returns_the_program():
    gateway = FakeGateway(
        '{"verdict":"approve","code":"","notes":"ok"}',
        execute_error=OtariError(403, "code execution is disabled", code="forbidden"),
    )
    result = run("add", _settings(), gateway, coder_model="openai/gpt-oss-20b", review_model="auto")
    assert "print(1 + 1)" in result["code"]
    assert result["execution"]["error"]["code"] == "forbidden"


def test_mock_result_is_labeled_and_has_no_cost():
    result = mock_result("say hello", _settings(), coder_model="openai/gpt-oss-20b", review_model="auto")
    assert result["mode"] == "mock"
    assert "Otari was not called" in result["mock_notice"]
    assert result["usage"]["cost_usd"] is None
    assert result["execution"]["source"] == "mock"
    assert "say hello" in result["code"]
