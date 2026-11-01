"use strict";

// The light novel tab (DEC-0250): the novel edition's flow on one screen. Seeds, plans, beat requests, checks and
// assembly go straight through; confirming a value, confirming the plan and locking a beat take the code the shell
// prints in its own terminal. No model is called here: the person's assistant writes each beat.
const $ = (id) => document.getElementById(id);
const STEP_MARKS = ["①", "②", "③", "④", "⑤", "⑥", "⑦"];

function showError(message) {
  $("error").textContent = message || "";
}

async function api(path, payload) {
  const secret = window.SYNAPSE_UI_CONFIG?.sessionSecret;
  const response = await fetch(path, {
    method: payload === undefined ? "GET" : "POST",
    headers: payload === undefined ? {} : { "Content-Type": "application/json", ...(secret ? { "X-Synapse-Session": secret } : {}) },
    body: payload === undefined ? undefined : JSON.stringify(payload),
  });
  const data = await response.json();
  if (response.status === 428 && data.needs_code) return confirmWithCode(data.action, { route: path, payload });
  if (!response.ok) throw new Error(data.error || "요청 실패 (" + response.status + ")");
  return data;
}

function askCode(what) {
  return new Promise((resolve) => {
    const dialog = document.createElement("dialog");
    dialog.className = "code-dialog";
    const form = document.createElement("form");
    form.method = "dialog";
    const title = document.createElement("h3");
    title.textContent = "확인 코드";
    const detail = document.createElement("p");
    detail.textContent = what;
    const where = document.createElement("p");
    where.className = "muted";
    where.textContent = "셸을 띄운 창에 방금 찍힌 6자리 코드를 적으세요.";
    const codeLabel = document.createElement("label");
    codeLabel.textContent = "확인 코드";
    const code = document.createElement("input");
    code.inputMode = "numeric";
    code.autocomplete = "one-time-code";
    code.required = true;
    codeLabel.append(code);
    const wordsLabel = document.createElement("label");
    wordsLabel.textContent = "본인 말(3자 이상)";
    const words = document.createElement("input");
    words.autocomplete = "off";
    wordsLabel.append(words);
    const row = document.createElement("div");
    row.className = "button-row";
    const cancel = document.createElement("button");
    cancel.type = "button";
    cancel.className = "secondary";
    cancel.textContent = "취소";
    cancel.addEventListener("click", () => dialog.close("cancel"));
    const ok = document.createElement("button");
    ok.value = "ok";
    ok.className = "primary";
    ok.textContent = "확정";
    row.append(cancel, ok);
    form.append(title, detail, where, codeLabel, wordsLabel, row);
    dialog.append(form);
    document.body.append(dialog);
    dialog.addEventListener("close", () => {
      const answer = dialog.returnValue === "ok" ? { code: code.value, words: words.value } : null;
      dialog.remove();
      resolve(answer);
    });
    dialog.showModal();
    code.focus();
  });
}

async function confirmWithCode(action, args) {
  const pending = await api("/api/confirm/request", { action, args });
  const typed = await askCode(pending.what);
  if (typed === null) throw new Error("확정을 취소했습니다.");
  const done = await api("/api/confirm/complete", { pending_id: pending.pending_id, code: typed.code, words: typed.words });
  return done.record;
}

function show(id, value) {
  const element = $(id);
  element.hidden = false;
  element.textContent = typeof value === "string" ? value : JSON.stringify(value, null, 2);
}

function renderSteps(status) {
  const bar = $("flow-steps");
  bar.replaceChildren();
  const marks = {};
  status.steps.forEach((step, index) => {
    marks[step.id] = STEP_MARKS[index] + " " + step.name;
    const link = document.createElement("a");
    link.href = "#";
    link.className = "flow-step" + (step.id === status.current ? " current" : "");
    if (step.id === status.current) link.setAttribute("aria-current", "step");
    link.textContent = marks[step.id];
    link.addEventListener("click", (event) => {
      event.preventDefault();
      document.querySelector(`[data-steps~="${step.id}"]`)?.scrollIntoView({ behavior: "smooth", block: "start" });
    });
    bar.append(link);
  });
  for (const section of document.querySelectorAll("[data-steps]")) {
    const heading = section.querySelector("h2");
    heading.querySelector(".step-badge")?.remove();
    const badge = document.createElement("span");
    badge.className = "step-badge";
    badge.textContent = section.dataset.steps.split(" ").map((id) => marks[id]).filter(Boolean).join(" · ");
    heading.prepend(badge);
  }
}

function renderValues(values) {
  const box = $("values");
  box.replaceChildren();
  for (const value of values) {
    const row = document.createElement("div");
    row.className = "value-row";
    const text = document.createElement("span");
    text.textContent = `${value.key} — ${value.text} (${value.status})`;
    row.append(text);
    if (value.status === "PROPOSED") {
      const button = document.createElement("button");
      button.type = "button";
      button.className = "secondary";
      button.textContent = "확정";
      button.addEventListener("click", () => guarded(() => api("/api/novel/value-confirm", { key: value.key })));
      row.append(button);
    }
    box.append(row);
  }
}

async function refresh() {
  const status = await api("/api/novel/status");
  $("project").textContent = status.project;
  renderSteps(status);
  renderValues(status.values || []);
  // Move on from a beat once it is locked: checking again would overwrite its saved text.
  const beats = status.flow?.beats || [];
  const firstOpen = beats.find((beat) => !beat.locked);
  const shown = beats.find((beat) => beat.scene === $("scene").value.trim() && beat.beat === Number($("beat").value));
  if (firstOpen && (!$("scene").value || shown?.locked)) {
    $("scene").value = firstOpen.scene;
    $("beat").value = firstOpen.beat;
  }
}

async function guarded(work) {
  showError("");
  try {
    const result = await work();
    await refresh();
    return result;
  } catch (error) {
    showError(error.message);
    return null;
  }
}

function parsed(id) {
  try {
    return JSON.parse($(id).value);
  } catch (_error) {
    throw new Error("JSON을 읽을 수 없습니다. 붙여 넣은 내용을 확인하세요.");
  }
}

const where = () => ({ scene: $("scene").value.trim(), beat: Number($("beat").value) });

$("save-seed").addEventListener("click", () => guarded(() => api("/api/novel/seed", { seed: parsed("seed-json") })));
$("save-plan").addEventListener("click", () => guarded(async () => show("plan-view", await api("/api/novel/plan", { plan: parsed("plan-json") }))));
$("confirm-plan").addEventListener("click", () => guarded(async () => show("plan-view", await api("/api/novel/plan-confirm", {}))));
$("request-beat").addEventListener("click", () => guarded(async () => {
  const request = await api("/api/novel/beat", where());
  const messages = request.request?.messages || request.messages || [];
  show("beat-request", messages.length ? messages.map((message) => `[${message.role}]\n${message.content}`).join("\n\n") : request);
}));
$("check-beat").addEventListener("click", () => guarded(async () => show("check-view", await api("/api/novel/check", { ...where(), text: $("beat-text").value }))));
$("lock-beat").addEventListener("click", () => guarded(async () => {
  show("check-view", await api("/api/novel/lock", where()));
  $("beat-text").value = "";
  $("beat-request").hidden = true;
}));
$("assemble").addEventListener("click", () => guarded(async () => {
  const done = await api("/api/novel/assemble", { scene: where().scene });
  $("assembled").textContent = "새 파일: " + done.assembled;
}));

refresh().catch((error) => showError(error.message));
