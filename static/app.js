const form = document.querySelector("#form");
const task = document.querySelector("#task");
const coderInput = document.querySelector("#coder-model");
const reviewInput = document.querySelector("#review-model");
const mockInput = document.querySelector("#mock");
const generate = document.querySelector("#generate");
const banner = document.querySelector("#mode-banner");
const result = document.querySelector("#result");
const modelList = document.querySelector("#model-list");
const modelHint = document.querySelector("#model-hint");

function setLink(id, href) {
  const node = document.querySelector(id);
  if (node && href) node.href = href;
}

function text(id, value) {
  document.querySelector(id).textContent = value || "";
}

async function loadConfig() {
  const response = await fetch("/api/config");
  const config = await response.json();
  coderInput.value = config.coder_model;
  reviewInput.value = config.review_model;
  const live = config.mode === "live";
  mockInput.checked = !live;
  mockInput.disabled = !config.has_api_key;
  banner.hidden = live;
  banner.textContent = live
    ? ""
    : "Mock mode. No OTARI_API_KEY is set, so Generate stays on this machine and does not call Otari. Copy .env.example to .env and add a workspace token to go live.";
  setLink("#link-otari", config.links.otari);
  setLink("#link-github", config.links.github);
  setLink("#link-activity", config.links.activity);
  setLink("#link-budgets", config.links.budgets);
  modelHint.textContent = live
    ? `Gateway ${config.base_url} · auth ${config.auth}. Review "auto" picks the cheapest other chat model, else ${config.review_fallback}.`
    : `Defaults: coder ${config.coder_model}, review ${config.review_model}. Gateway ${config.base_url}.`;
  if (live) loadModels();
}

async function loadModels() {
  const response = await fetch("/api/models");
  const payload = await response.json();
  const models = payload.models || [];
  modelList.replaceChildren();
  for (const model of models) {
    if (model.type && model.type !== "chat") continue;
    const option = document.createElement("option");
    option.value = model.id;
    modelList.append(option);
  }
  if (models.length) {
    modelHint.textContent += ` ${models.length} models visible to this key.`;
  }
}

function row(label, value) {
  const line = document.createElement("div");
  line.className = "row";
  const name = document.createElement("strong");
  name.textContent = label;
  const body = document.createElement("span");
  body.textContent = value == null || value === "" ? "—" : String(value);
  line.append(name, body);
  return line;
}

function renderUsage(payload) {
  const box = document.querySelector("#usage");
  box.replaceChildren();
  const usage = payload.usage || {};
  box.append(
    row("Coder", payload.coder.model),
    row("Coder request", payload.coder.request_id),
    row("Provider", payload.coder.provider),
    row("Served model", payload.coder.served_model),
    row("Review", payload.review.model),
    row("Review pick", payload.review.selection),
    row("Review request", payload.review.request_id),
    row("Prompt tokens", usage.prompt_tokens),
    row("Completion tokens", usage.completion_tokens),
    row("Total tokens", usage.total_tokens),
    row("cost_usd", usage.cost_usd),
    row("Pricing", (usage.pricing_sources || []).join(", ")),
  );
  if (usage.note) box.append(row("Note", usage.note));
  const windowUsage = payload.usage_window;
  if (windowUsage && !windowUsage.error) {
    box.append(
      row("Window cost", windowUsage.cost),
      row("Window tokens", windowUsage.total_tokens),
      row("Window requests", windowUsage.request_count),
    );
  } else if (windowUsage && windowUsage.error) {
    box.append(row("Usage API", windowUsage.error.detail || windowUsage.error.error));
  }
  const pass = payload.coder.usage;
  if (pass) {
    box.append(row("Coder cost_usd", pass.cost_usd), row("Coder pricing_source", pass.pricing_source));
  }
  const reviewUsage = payload.review.usage;
  if (reviewUsage) {
    box.append(row("Review cost_usd", reviewUsage.cost_usd));
  }
}

function renderBudgets(payload) {
  const box = document.querySelector("#budgets");
  box.replaceChildren();
  const intro = document.createElement("p");
  intro.className = "meta";
  const activity = payload.links.activity_request || payload.links.activity;
  intro.append("Activity and spend live in the Otari dashboard. ");
  const activityLink = document.createElement("a");
  activityLink.href = activity;
  activityLink.textContent = "Open Activity";
  activityLink.target = "_blank";
  activityLink.rel = "noreferrer";
  const budgetsLink = document.createElement("a");
  budgetsLink.href = payload.links.budgets;
  budgetsLink.textContent = "Open budgets";
  budgetsLink.target = "_blank";
  budgetsLink.rel = "noreferrer";
  intro.append(activityLink, " · ", budgetsLink);
  box.append(intro);
  const sources = (payload.budgets && payload.budgets.sources) || [];
  if (!sources.length) {
    const empty = document.createElement("p");
    empty.textContent = payload.mode === "mock"
      ? "Budgets are not loaded in mock mode. Set a budget in the Otari dashboard before a live demo."
      : "No budget payload on this response.";
    box.append(empty);
    return;
  }
  for (const source of sources) {
    const title = document.createElement("p");
    title.className = "meta";
    title.textContent = source.source;
    box.append(title);
    if (source.error) {
      box.append(row("Not readable", source.detail || source.error));
      continue;
    }
    if (!source.budgets.length) {
      box.append(row("Budgets", "none returned"));
      continue;
    }
    for (const budget of source.budgets) {
      const spend = budget.total_spend == null ? "" : `, spend ${budget.total_spend}`;
      const cap = budget.max_budget == null ? "no USD cap" : `$${budget.max_budget}`;
      box.append(row(budget.name || budget.budget_id || "budget", `${cap}${spend}`));
    }
  }
}

function renderTools(calls) {
  const box = document.querySelector("#tools");
  box.replaceChildren();
  if (!calls || !calls.length) {
    const empty = document.createElement("p");
    empty.className = "meta";
    empty.textContent = "The coder did not run a gateway tool on this request. The final program still goes to POST /api/v1/code/execute.";
    box.append(empty);
    return;
  }
  for (const call of calls) {
    const block = document.createElement("div");
    block.className = "call";
    const title = document.createElement("p");
    title.textContent = `${call.type || "tool"} · ${call.status || ""} · exit ${call.exit_code ?? "—"}`;
    const pre = document.createElement("pre");
    pre.className = "code";
    pre.textContent = call.stdout || call.stderr || call.code || "";
    block.append(title, pre);
    box.append(block);
  }
}

function render(payload) {
  result.hidden = false;
  const mockFlag = document.querySelector("#mock-flag");
  mockFlag.hidden = payload.mode !== "mock";
  text("#code", payload.code || "");
  const execution = payload.execution || {};
  text(
    "#exec-meta",
    `${execution.source || ""} · return ${execution.return_code ?? "—"} · request ${execution.request_id || "—"}`,
  );
  text("#stdout", execution.stdout || "(no stdout)");
  const stderr = document.querySelector("#stderr");
  stderr.hidden = !execution.stderr;
  stderr.textContent = execution.stderr || "";
  const execError = document.querySelector("#exec-error");
  if (execution.error) {
    execError.hidden = false;
    execError.textContent = execution.error.detail || execution.error.error;
    if (execution.error.code === "budget_exceeded" || execution.error.error === "budget_exceeded") {
      execError.textContent += " Budget blocked this sandbox call. Open Budgets in the Otari dashboard.";
    }
  } else {
    execError.hidden = true;
    execError.textContent = "";
  }
  const review = payload.review || {};
  const reviewError = review.error ? ` Error: ${review.error.detail || review.error.error}` : "";
  text(
    "#review",
    `${review.verdict || "not run"} — ${review.notes || ""}${reviewError}`.trim(),
  );
  renderTools(payload.coder.tool_calls);
  renderUsage(payload);
  renderBudgets(payload);
  const passLinks = document.querySelector("#pass-links");
  passLinks.replaceChildren();
  if (payload.links.activity_request) {
    const anchor = document.createElement("a");
    anchor.href = payload.links.activity_request;
    anchor.target = "_blank";
    anchor.rel = "noreferrer";
    anchor.textContent = "This request in Activity";
    passLinks.append(anchor);
  }
}

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  generate.disabled = true;
  generate.textContent = "Routing…";
  try {
    const response = await fetch("/api/generate", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({
        task: task.value,
        coder_model: coderInput.value,
        review_model: reviewInput.value,
        mock: mockInput.checked,
      }),
    });
    const payload = await response.json();
    if (!response.ok) {
      result.hidden = false;
      document.querySelector("#mock-flag").hidden = true;
      text("#code", "");
      text("#stdout", "");
      text("#review", "");
      const execError = document.querySelector("#exec-error");
      execError.hidden = false;
      execError.textContent = payload.detail || payload.error || response.statusText;
      if (payload.code === "budget_exceeded" || payload.error === "budget_exceeded") {
        execError.textContent += ` Scope: ${payload.budget_scope || "budget"}.`;
      }
      return;
    }
    render(payload);
  } catch (error) {
    result.hidden = false;
    const execError = document.querySelector("#exec-error");
    execError.hidden = false;
    execError.textContent = error.message;
  } finally {
    generate.disabled = false;
    generate.textContent = "Generate";
  }
});

loadConfig();
