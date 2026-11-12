from pathlib import Path

from fastapi.testclient import TestClient

from coderoute.app import app


def test_env_example_has_no_secret():
    text = Path(".env.example").read_text()
    assert "OTARI_API_KEY=\n" in text
    assert "OTARI_BASE_URL=https://api.otari.ai/api/v1" in text
    assert "OTARI_AUTH=bearer" in text
    assert "otk_" not in text.split("OTARI_API_KEY=", 1)[1].split("\n", 1)[0]


def test_mock_generate_and_pages(monkeypatch):
    monkeypatch.delenv("OTARI_API_KEY", raising=False)
    monkeypatch.delenv("OTARI_MOCK", raising=False)
    client = TestClient(app)
    page = client.get("/")
    assert page.status_code == 200
    assert "Generate" in page.text
    assert "Mock mode" in page.text

    health = client.get("/api/health").json()
    assert health["mode"] == "mock"

    missing = client.post("/api/generate", json={"task": "print hi", "mock": False})
    assert missing.status_code == 400
    assert missing.json()["error"] == "missing_api_key"

    generated = client.post("/api/generate", json={"task": "Print a greeting."})
    assert generated.status_code == 200
    body = generated.json()
    assert body["mode"] == "mock"
    assert body["execution"]["source"] == "mock"
    assert "Print a greeting." in body["code"]
    assert body["links"]["activity"].endswith("/requests")
    assert body["usage"]["cost_usd"] is None

    models = client.get("/api/models").json()
    assert models["mode"] == "mock"
    assert models["models"] == []
