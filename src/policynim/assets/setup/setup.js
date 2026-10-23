"use strict";

const token = new URLSearchParams(location.hash.slice(1)).get("token") || "";
history.replaceState(null, "", location.pathname);
const byId = (id) => document.getElementById(id);
let reviewDigest = "";
let connected = false;
let built = false;
let verified = false;
let busy = false;

function updateActions() {
  const consent = byId("consent").checked;
  for (const field of document.querySelectorAll("input, select, textarea")) field.disabled = busy;
  for (const button of document.querySelectorAll("[data-action]")) {
    const action = button.dataset.action;
    button.disabled = busy || (action === "probe" && !reviewDigest)
      || (action === "build" && (!reviewDigest || !connected || !consent))
      || (action === "verify" && (!built || !consent))
      || (action === "save" && !verified);
  }
}

function choices() {
  return {
    deployment: document.querySelector("input[name=deployment]:checked").value,
    source_mode: byId("source-mode").value,
    topics: Array.from(document.querySelectorAll("input[name=topic]:checked"), (item) => item.value),
    additional_packs: byId("extra-packs").value.split("\n").map((item) => item.trim()).filter(Boolean),
    source_path: byId("source-path").value,
    generations_dir: byId("generations-dir").value,
    vector_backend: byId("vector-backend").value,
    vector_url: byId("vector-backend").value === "sqlite" ? "" : byId("vector-url").value,
    vector_namespace: byId("vector-namespace").value,
    vector_api_key: byId("vector-api-key").value,
    vector_password: byId("vector-password").value,
    nvidia_api_key: byId("nvidia-api-key").value,
    task: byId("task").value,
    review_digest: reviewDigest,
    consent: byId("consent").checked,
  };
}

function updateFields() {
  const source = byId("source-mode").value;
  const backend = byId("vector-backend").value;
  const shared = document.querySelector("input[name=deployment]:checked").value === "shared_hosted";
  byId("custom-setup").hidden = shared;
  byId("shared-note").hidden = !shared;
  byId("topic-options").hidden = source !== "topics";
  byId("source-path-field").hidden = source !== "profile" && source !== "directory";
  byId("extra-packs-field").hidden = source === "profile" || source === "directory";
  if (byId("extra-packs-field").hidden) byId("extra-packs").value = "";
  byId("remote-fields").hidden = backend === "sqlite";
  byId("qdrant-key-field").hidden = backend !== "qdrant";
  byId("pg-password-field").hidden = backend !== "pgvector";
  byId("pg-extra").hidden = backend !== "pgvector";
  byId("url-hint").textContent = backend === "pgvector"
    ? "Use postgresql://user@host/database?sslmode=verify-full. Loopback can use sslmode=disable."
    : "Use HTTPS, or HTTP on loopback. Keep credentials out of the URL.";
}

function showText(id, text) {
  const region = byId(id);
  region.hidden = false;
  region.replaceChildren();
  const pre = document.createElement("pre");
  pre.textContent = text;
  region.append(pre);
}

function renderCatalog(catalog) {
  const region = byId("catalog");
  region.hidden = false;
  region.replaceChildren();
  const summary = document.createElement("p");
  summary.textContent = `${catalog.effective_count} effective policies. Source digest: ${catalog.digest}`;
  region.append(summary);
  for (const entry of catalog.policies) {
    const detail = document.createElement("details");
    const title = document.createElement("summary");
    title.textContent = `${entry.policy.policy_id} - ${entry.policy.title} (${entry.state})`;
    const body = document.createElement("pre");
    body.textContent = `${entry.target_digest}\n${entry.reason || ""}\n${entry.body}`;
    detail.append(title, body);
    region.append(detail);
  }
}

function renderResult(answer) {
  const region = byId("policy-result");
  region.hidden = false;
  region.replaceChildren();
  const summary = document.createElement("p");
  summary.textContent = `${answer.plan_steps.length} complete policy sources. Verified generation: ${answer.generation_id}`;
  region.append(summary);
  answer.plan_steps.forEach((body, index) => {
    const detail = document.createElement("details");
    const title = document.createElement("summary");
    title.textContent = `Read complete policy source ${index + 1}`;
    const source = document.createElement("pre");
    source.textContent = body;
    detail.append(title, source);
    region.append(detail);
  });
  const coordinates = document.createElement("details");
  const title = document.createElement("summary");
  title.textContent = `${answer.citations.length} source references`;
  const sources = document.createElement("pre");
  sources.textContent = answer.citations.map((item) => `${item.policy_id}: ${item.path}:${item.lines}`).join("\n");
  coordinates.append(title, sources);
  region.append(coordinates);
}

async function request(action) {
  byId("status").textContent = `${action === "build" ? "Building a generation" : "Working"}...`;
  busy = true;
  updateActions();
  byId("setup-form").setAttribute("aria-busy", "true");
  try {
    const response = await fetch(`/api/${action}`, {
      method: "POST", headers: { "Content-Type": "application/json", "X-Setup-Token": token },
      body: JSON.stringify(choices()),
    });
    const result = await response.json();
    if (!response.ok) throw new Error(result.message || "Setup didn't finish.");
    if (action === "preview") {
      reviewDigest = result.review_digest;
      built = Boolean(result.generation_id);
      verified = Boolean(result.verified);
      renderCatalog(result.catalog);
    } else if (action === "probe") {
      connected = true;
      byId("connection").textContent = "Connected. Generation compatibility will be verified during build.";
    } else if (action === "build") {
      built = true;
      verified = false;
      showText("generation", `Active generation: ${result.generation_id}\n${result.chunk_count} chunks\n${(result.warnings || []).join("\n")}`);
    } else if (action === "verify") {
      verified = true;
      renderResult(result.result);
    } else if (action === "save") {
      showText("saved", `Saved: ${result.config_path}\n\n${result.commands.join("\n\n")}\n${result.warnings.join("\n")}`);
    }
    const next = { preview: "Next: check storage connectivity.", probe: "Next: review consent and build.",
      build: "Next: verify policy evidence for a task.", verify: "Next: inspect sources and save configuration.",
      save: "Configuration saved. Use the commands above to connect your agent." };
    byId("status").textContent = next[action];
  } catch (error) {
    if (action === "verify" || action === "build") verified = false;
    byId("status").textContent = error.message || "Setup didn't finish. Check the current generation before retrying.";
  } finally {
    busy = false;
    updateActions();
    byId("setup-form").removeAttribute("aria-busy");
  }
}

document.querySelectorAll("[data-action]").forEach((button) => {
  button.addEventListener("click", () => request(button.dataset.action));
});
byId("setup-form").addEventListener("submit", (event) => event.preventDefault());
byId("setup-form").addEventListener("change", (event) => {
  updateFields();
  if (!["task", "consent"].includes(event.target.id)) {
    reviewDigest = "";
    connected = built = verified = false;
    byId("connection").textContent = "";
    for (const id of ["catalog", "generation", "policy-result", "saved"]) byId(id).hidden = true;
    byId("status").textContent = "Choices changed. Preview the selection before continuing.";
  }
  updateActions();
});
updateFields();
updateActions();
if (!token) byId("launch-error").hidden = false;
if (token) {
  fetch("/api/status", { headers: { "X-Setup-Token": token } })
    .then(async (response) => {
      if (!response.ok) throw new Error("Setup authorization expired. Open the launch URL again.");
      return response.json();
    })
    .then((state) => {
      byId("generations-dir").value = state.generations_dir;
      if (state.api_key_configured) byId("key-hint").textContent = "A key is configured. Leave blank to keep using it.";
      if (state.generation_id) showText("generation", `This setup session built ${state.generation_id}. Re-enter the same choices and preview to continue.`);
    })
    .catch((error) => { byId("status").textContent = error.message; });
}
