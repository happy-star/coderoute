# CodeRoute

A multi-model coding assistant routed through [Otari](https://otari.ai) that turns a natural-language task into runnable Python using open-weight models, Otari code execution, and usage budgets.

CodeRoute is a small demo for the Mozilla Otari Developers Wilding hackathon. It calls the hosted gateway with the official OpenAI Python SDK. The gateway project is [mozilla-ai/otari](https://github.com/mozilla-ai/otari).

## What it is

You type a task. CodeRoute:

1. Asks an open-weight coder model (`openai/gpt-oss-20b` by default) to write one Python program. The request enables Otari's server-side code execution tool, so the model can run the program inside the gateway while it writes.
2. Asks a second model to review that program and, when needed, replace it. `OTARI_REVIEW_MODEL=auto` picks the cheapest other chat model your key can see.
3. Runs the final program with Otari's sandbox (`POST /api/v1/code/execute`) and shows stdout, stderr, and the exit status.
4. Shows the usage fields the gateway returns (`usage.cost_usd`, `usage.pricing_source`, token counts, `Otari-Request-ID`) and links each request to the dashboard Activity log. Budget caps from the API are listed when the key is allowed to read them.

If `OTARI_API_KEY` is empty, the page stays in **mock mode**. Mock output is labeled and is not an Otari response.

## Otari features used

Checked against the Wilding rubric and the current docs (hosted quickstart, built-in tools, code execution, budgets). Calls match the hosted OpenAPI on gateway `0.38.0`.

- [x] **Open-weight model through the hosted gateway.** `POST /api/v1/chat/completions` with the OpenAI SDK `base_url` set to `https://api.otari.ai/api/v1`. Default coder id: `openai/gpt-oss-20b`.
- [x] **Code execution (server-side tool).** The coder request sends `tools: [{"type": "otari_code_execution"}]`, `max_tool_iterations: 3`, and the header `Otari-Code-Execution: otari`. Tool runs come back on `otari_tool_calls` (`stdout`, `stderr`, `exit_code`). See [Built-in tools](https://docs.otari.ai/otari/for-integrators/tools).
- [x] **Direct sandbox run.** The final program is sent to `POST /api/v1/code/execute` (`code`, `timeout_seconds`). See [Code execution](https://docs.otari.ai/otari/for-integrators/api-reference/code-execution).
- [x] **Usage.** Each completion's `usage` object is shown, including `cost_usd` and `pricing_source` when the gateway sends them. Response headers `Otari-Request-ID`, `Otari-Model`, and `Otari-Provider` are shown. `GET /api/v1/usage/summary` fills the account window when the key can read it.
- [x] **Budgets.** The page links to the dashboard budgets screen and, when the key is allowed, reads `GET /api/v1/organizations/me/budgets` and `GET /api/v1/budgets` (`max_budget`, `total_spend`). A `403` with `code: budget_exceeded` is shown with `Otari-Budget-Scope`. Set caps in the dashboard: [Budgets](https://docs.otari.ai/learn/budgets).
- [x] **Activity.** Each live request links to `https://otari.ai/dashboard/requests?request=<Otari-Request-ID>`. `/dashboard/activity` is the legacy alias and redirects to `/requests`.
- [x] **Second model (review / route).** The review pass is a different model. `auto` chooses the lowest `output_price_per_million` among other chat models from `GET /api/v1/models`. If prices are missing, it uses `OTARI_REVIEW_FALLBACK` (`openai/gpt-oss-safeguard-20b`).
- [x] **No secrets in git.** The key is read from the environment. `.env.example` ships with `OTARI_API_KEY` empty.

### Enable code execution

The hosted gateway has to allow the sandbox for your workspace. In the dashboard:

1. Open the workspace.
2. Go to **Tools → Code execution** ([configure code execution](https://docs.otari.ai/tools/configure-code-execution)).
3. Set code execution to **Allowed** (or follow the deployment default, if that default already allows it).

A workspace with code execution blocked returns an error on the tool and on `POST /api/v1/code/execute`. CodeRoute still shows the program and the gateway's error text. Direct execution is priced and budgeted as `otari_code_execution`.

### Auth and base URL

Documented hosted quickstart ([docs](https://docs.otari.ai/getting-started/quickstart)):

```bash
curl https://api.otari.ai/api/v1/chat/completions \
  -H "Authorization: Bearer $OTARI_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"model":"openai/gpt-oss-20b","messages":[{"role":"user","content":"Say hello."}]}'
```

The live gateway's OpenAPI security scheme is `Otari-Key`. A check against `https://api.otari.ai` (gateway `0.38.0`) accepted both headers: a missing credential names `Otari-Key`, `Authorization`, and `x-api-key`, and an invalid key returns `{"detail":"Invalid API key"}` for either `Authorization: Bearer` or `Otari-Key`. CodeRoute defaults to Bearer so the OpenAI SDK matches the quickstart. Set `OTARI_AUTH=otari-key` to send only `Otari-Key`.

EU accounts use `https://eu.api.otari.ai/api/v1`. `api.otari.ai` answers an EU key with `421` and names that host. Put the EU host in `OTARI_BASE_URL`.

`https://otari.ai/api/v1` answered an unauthenticated call with the same error body. The documented client base, and the one this app uses, is `https://api.otari.ai/api/v1`.

Model ids are catalog ids from the Models tab (`GET /api/v1/models`). `openai/gpt-oss-20b` is the open-weight default. If your key cannot call it, pick another id from the dropdown.

## Setup

Python 3.11 or newer.

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

Edit `.env`:

- `OTARI_API_KEY` — workspace token from [otari.ai](https://otari.ai) → API Keys. It starts with `otk_` and is shown once. Leave it empty to stay in mock mode.
- `OTARI_BASE_URL` — `https://api.otari.ai/api/v1` (already set).
- `OTARI_AUTH` — `bearer` or `otari-key`.
- `OTARI_CODER_MODEL` and `OTARI_REVIEW_MODEL` — catalog ids, or `auto` for the review pass.

Start the app:

```bash
python3 -m uvicorn coderoute.app:app --host 0.0.0.0 --port 8000
```

`npm start` runs the same command and honors `PORT`. Open http://localhost:8000.

The license is the MIT license already in `LICENSE`.

## Testing

```bash
pip install -r requirements.txt
pytest
```

Mock server, no key:

```bash
python3 -m uvicorn coderoute.app:app --port 8000
curl -s localhost:8000/api/health
curl -s localhost:8000/api/generate \
  -H 'content-type: application/json' \
  -d '{"task":"Print the first 10 Fibonacci numbers."}'
```

The JSON `mode` is `mock`, `execution.source` is `mock`, and `usage.cost_usd` is `null`.

Live call (spends from your Otari balance; keep the task short):

```bash
# .env contains OTARI_API_KEY
curl -s localhost:8000/api/generate \
  -H 'content-type: application/json' \
  -d '{"task":"Print the first 10 Fibonacci numbers.","mock":false}'
```

Expect `mode: live`, a Python program, `execution.return_code`, two different `coder.model` and `review.model` values, and `usage.cost_usd` when the gateway priced the calls. Then open the `links.activity_request` URL and confirm the row in the dashboard.

`POST /api/generate` with `"mock": false` and no key returns `400 missing_api_key`.

## Demo script (about 4 minutes)

Record the screen with a workspace key that can call two chat models and run code. Set a small budget first so the Activity and Budgets pages have something to show.

1. **0:00 — Product.** Show this README one-liner and the checklist above. Mention [otari.ai](https://otari.ai) and [mozilla-ai/otari](https://github.com/mozilla-ai/otari).
2. **0:30 — No secrets.** Show `.env.example` with `OTARI_API_KEY` empty, then the dashboard API Keys tab where the `otk_` token is created. Do not read the token aloud.
3. **1:00 — Mock.** Start without a key. The banner says mock mode. Submit “Print the first 10 Fibonacci numbers.” Point at the mock flag and that cost is empty.
4. **1:40 — Live route.** Restart with the key. Submit the same short task. Walk the program, the sandbox stdout, and any `otari_tool_calls` from the coder pass.
5. **2:40 — Second model.** Show the coder id and the review id side by side, plus `usage.cost_usd` and the two `Otari-Request-ID`s.
6. **3:20 — Activity and budgets.** Click “This request in Activity” (`/dashboard/requests?request=…`). Open Budgets and point at the cap that would stop a later call.
7. **4:00 — If the sandbox is off.** If execution returns an error, open Tools → Code execution, set it to Allowed, and rerun once. Stop there so the recording stays inside five minutes.

Keep tasks to a few lines of standard-library Python. The review pass and the sandbox call are billed separately from the coder.
