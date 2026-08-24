"use strict";

const $ = (id) => document.getElementById(id);
const els = {
  form: $("ask-form"), question: $("question"), askBtn: $("ask-btn"),
  stopBtn: $("stop-btn"),
  coldstart: $("coldstart"), stages: $("stages"),
  sqlcard: $("sqlcard"), sql: $("sql-preview"), guardNote: $("guard-note"),
  attemptBadge: $("attempt-badge"),
  approveBtn: $("approve-btn"), rejectBtn: $("reject-btn"),
  resultcard: $("resultcard"), chartbox: $("chartbox"), tablebox: $("tablebox"),
  stats: $("stats"), live: $("live"), conn: $("conn"),
  errorcard: $("errorcard"), errormsg: $("errormsg"),
  backdrop: $("modal-backdrop"), modal: $("modal"),
  confirmStop: $("confirm-stop"), cancelStop: $("cancel-stop"),
};

let phase = "idle";
let runId = null;
let lastQuestion = "";
let lastFocus = null;

function announce(text) { els.live.textContent = text; }
function show(el, on = true) { el.classList.toggle("hidden", !on); }

function resetOutput() {
  show(els.sqlcard, false); show(els.resultcard, false);
  show(els.errorcard, false); show(els.attemptBadge, false);
  els.sql.textContent = ""; els.tablebox.innerHTML = "";
  els.chartbox.innerHTML = ""; els.stats.textContent = "";
  els.guardNote.textContent = ""; els.question.value = "";
}

/* ---------- cold start ---------- */

async function pollStatus() {
  try {
    const res = await fetch("/api/status");
    const snap = await res.json();
    renderStages(snap.stages || {});
    if (snap.ready) {
      show(els.coldstart, false);
      phase = "idle";
      els.conn.textContent =
        `warehouse ready: ${snap.warehouse_tables} tables, model ${snap.model}`;
      els.question.focus();
      return;
    }
  } catch { /* server not accepting yet; keep polling */ }
  setTimeout(pollStatus, 1500);
}

function renderStages(stages) {
  show(els.coldstart, true);
  els.stages.innerHTML = "";
  for (const [name, info] of Object.entries(stages)) {
    const li = document.createElement("li");
    li.className = info.state === "done" ? "done" :
      info.state === "running" ? "running" : "";
    li.textContent = name === "boot" ? "service booting"
      : name === "warehouse" ? "connecting to the warehouse"
      : "loading schema catalog";
    els.stages.appendChild(li);
  }
}

/* ---------- SSE over POST ---------- */

async function ssePost(url, body, onEvent) {
  const res = await fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!res.ok || !res.body) {
    let detail = res.statusText;
    try { detail = (await res.json()).detail || detail; } catch {}
    throw new Error(detail);
  }
  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    let sep;
    while ((sep = buffer.indexOf("\n\n")) !== -1) {
      const chunk = buffer.slice(0, sep); buffer = buffer.slice(sep + 2);
      let event = "message", data = "";
      for (const line of chunk.split("\n")) {
        if (line.startsWith("event: ")) event = line.slice(7).trim();
        else if (line.startsWith("data: ")) data += line.slice(6);
      }
      if (data) onEvent(event, JSON.parse(data));
    }
  }
}

/* ---------- asking ---------- */

els.form.addEventListener("submit", (e) => {
  e.preventDefault();
  if (phase !== "idle") return;
  const text = els.question.value.trim();
  if (text.length < 3) return;
  lastQuestion = text;
  resetOutput();
  setPhase("generating");
  runQuery(text).catch(showError);
});

function setPhase(next) {
  phase = next;
  els.askBtn.disabled = next !== "idle";
  els.question.disabled = next === "generating";
  show(els.stopBtn, next === "generating" || next === "executing");
  announce(
    next === "generating" ? "Writing SQL." :
    next === "preview" ? "Waiting for your approval." :
    next === "executing" ? "Running the query." :
    next === "stopping" ? "Stopping." : "Ready."
  );
}

function showError(message) {
  els.errormsg.textContent = message;
  show(els.errorcard, true);
  setPhase("idle");
}

async function runQuery(text) {
  let sqlText = "";
  await ssePost("/api/query", { question: text }, (event, data) => {
    if (event === "meta") { runId = data.run_id; return; }
    if (event === "token") {
      sqlText += data.text;
      els.sql.textContent = sqlText;
      show(els.sqlcard, true);
      return;
    }
    if (event === "stopped") { showError("Stopped before finishing."); return; }
    if (event === "error") { showError(data.message || "generation failed"); return; }
    if (event === "guard_blocked") {
      els.guardNote.textContent =
        `Static check rejected this (${data.reason}); asked for a rewrite.`;
      sqlText = "";
      return;
    }
    if (event === "preview" || event === "correction_preview") {
      presentPreview(event, data);
    }
  });
}

function presentPreview(event, data) {
  els.sql.textContent = data.sql;
  show(els.sqlcard, true);
  if (data.attempts > 1) {
    els.attemptBadge.textContent = `attempt ${data.attempts} after correction`;
    show(els.attemptBadge, true);
  }
  if (event === "correction_preview") {
    els.guardNote.textContent = `Previous attempt failed in Postgres (${data.prior_error}). This replacement needs your approval too.`;
  } else if (data.blocked) {
    els.guardNote.textContent =
      `Still rejected by policy (${data.reason}). You can approve to see it refused by the database, or reject.`;
  } else if (!data.naive_allowed) {
    els.guardNote.textContent =
      "Note: the keyword-only filter disliked something; deeper layers cleared it.";
  } else {
    els.guardNote.textContent = "";
  }
  setPhase("preview");
  els.approveBtn.focus();
}

/* ---------- approve / reject ---------- */

els.approveBtn.addEventListener("click", () => {
  if (!runId || phase !== "preview") return;
  setPhase("executing");
  els.resultcard.scrollIntoView({ block: "nearest" });
  approveRun().catch(showError);
});

els.rejectBtn.addEventListener("click", () => {
  if (!runId || phase !== "preview") return;
  fetch("/api/reject", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ run_id: runId }),
  });
  setPhase("idle");
  show(els.sqlcard, false);
  els.question.focus();
});

async function approveRun() {
  show(els.resultcard, true);
  announce("Running the query.");
  let rowsShown = 0;
  await ssePost("/api/approve", { run_id: runId }, (event, data) => {
    if (event === "columns") {
      renderSkeleton(columns = data.columns, lastQuestion);
      return;
    }
    if (event === "rows") {
      appendRows(data.rows);
      rowsShown += data.rows.length;
      announce(`${rowsShown} rows.`);
      return;
    }
    if (event === "chart") { renderChart(data.descriptor, data.title); return; }
    if (event === "stats") {
      els.stats.textContent =
        `${data.row_count} rows in ${data.wall_ms} ms` +
        (data.generate_ms ? `, SQL written in ${data.generate_ms} ms` : "");
      setPhase("idle");
      els.question.focus();
      return;
    }
    if (event === "stopped") { showError("Server-side query cancelled."); return; }
    if (event === "exec_error") {
      els.guardNote.textContent = `Database refused: ${data.sanitized}. Asking for a corrected statement.`;
      return;
    }
    if (event === "correction_preview") {
      show(els.resultcard, false);
      presentPreview(event, data);
    }
    if (event === "error") { showError(data.message || "failed"); }
  });
}

function esc(value) {
  const d = document.createElement("div");
  d.textContent = value == null ? "" : String(value);
  return d.innerHTML;
}

let skeletonRow = null;

function renderSkeleton(cols, questionText) {
  const table = document.createElement("table");
  const caption = document.createElement("caption");
  caption.textContent = `Results for: ${questionText}`;
  table.appendChild(caption);
  const head = document.createElement("thead");
  const tr = document.createElement("tr");
  cols.forEach((c) => {
    const th = document.createElement("th");
    th.scope = "col";
    th.textContent = c;
    tr.appendChild(th);
  });
  head.appendChild(tr); table.appendChild(head);
  const tbody = document.createElement("tbody");
  table.appendChild(tbody);
  els.tablebox.innerHTML = ""; els.tablebox.appendChild(table);
  skeletonRow = { table, tbody };
}

function appendRows(rows) {
  if (!skeletonRow) return;
  for (const row of rows) {
    const tr = document.createElement("tr");
    row.forEach((cell) => {
      const td = document.createElement("td");
      td.textContent = cell == null ? "" : String(cell);
      if (typeof cell === "number") td.className = "n";
      tr.appendChild(td);
    });
    skeletonRow.tbody.appendChild(tr);
  }
}

/* ---------- charts, drawn as plain SVG from a descriptor ---------- */

function renderChart(descriptor, title) {
  els.chartbox.innerHTML = "";
  if (!descriptor) return;
  const table = els.tablebox.querySelector("table");
  if (!table) return;
  const headers = [...table.querySelectorAll("thead th")].map((th) => th.textContent);
  const xi = headers.indexOf(descriptor.x);
  const yi = headers.indexOf(descriptor.y[0]);
  if (xi < 0 || yi < 0) return;
  const body = [...table.querySelectorAll("tbody tr")]
    .map((tr) => [...tr.children].map((td) => td.textContent));
  const points = body.map((r) => ({ x: r[xi], y: parseFloat(r[yi]) }))
    .filter((p) => Number.isFinite(p.y));

  const svg = descriptor.type === "bar" ? drawBar(points)
    : descriptor.type === "line" ? drawLine(points) : null;
  if (!svg) return;
  els.chartbox.appendChild(svg);
  const cap = document.createElement("p");
  cap.className = "chart-caption";
  cap.textContent = title || "";
  els.chartbox.appendChild(cap);
}

function drawBar(points) {
  const w = 460, h = 180, padL = 46, padB = 34;
  const max = Math.max(...points.map((p) => p.y), 0.000001);
  const bw = Math.max(8, (w - padL - 10) / points.length - 6);
  const parts = [`<line class="axis" x1="${padL}" y1="10" x2="${padL}" y2="${h - padB}" />`,
    `<line class="axis" x1="${padL}" y1="${h - padB}" x2="${w}" y2="${h - padB}" />`];
  points.forEach((p, i) => {
    const bh = Math.round((p.y / max) * (h - padB - 20));
    const x = padL + 6 + i * (bw + 6);
    parts.push(`<rect class="bar-rect" x="${x}" y="${h - padB - bh}" width="${bw}" height="${bh}"><title>${esc(p.x)}: ${p.y}</title></rect>`);
    if (points.length <= 14) {
      parts.push(`<text class="tick-label" x="${x + bw / 2}" y="${h - padB + 12}" text-anchor="middle">${esc(String(p.x).slice(0, 9))}</text>`);
    }
  });
  parts.push(`<text class="tick-label" x="${padL - 6}" y="16" text-anchor="end">${max}</text>`);
  const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  svg.setAttribute("viewBox", `0 0 ${w} ${h}`);
  svg.setAttribute("class", "chart");
  svg.setAttribute("role", "img");
  svg.innerHTML = parts.join("");
  return svg;
}

function drawLine(points) {
  const w = 460, h = 180, padL = 46, padB = 30;
  const ys = points.map((p) => p.y);
  const min = Math.min(...ys), max = Math.max(...ys);
  const span = max - min || 1;
  const stepX = (w - padL - 10) / Math.max(points.length - 1, 1);
  const coords = points.map((p, i) =>
    [padL + i * stepX, h - padB - ((p.y - min) / span) * (h - padB - 16)]);
  const path = coords.map((c, i) => `${i ? "L" : "M"}${c[0].toFixed(1)},${c[1].toFixed(1)}`).join(" ");
  const dots = coords.map((c, i) =>
    `<circle class="dot" cx="${c[0].toFixed(1)}" cy="${c[1].toFixed(1)}" r="2.5"><title>${esc(points[i].x)}: ${points[i].y}</title></circle>`).join("");
  const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  svg.setAttribute("viewBox", `0 0 ${w} ${h}`);
  svg.setAttribute("class", "chart");
  svg.setAttribute("role", "img");
  svg.innerHTML =
    `<line class="axis" x1="${padL}" y1="10" x2="${padL}" y2="${h - padB}" />` +
    `<line class="axis" x1="${padL}" y1="${h - padB}" x2="${w}" y2="${h - padB}" />` +
    `<path class="line-path" d="${path}" />` + dots;
  return svg;
}

/* ---------- stop, with a real dialog and focus handling ---------- */

document.addEventListener("keydown", (e) => {
  if (!els.backdrop.classList.contains("hidden")) {
    if (e.key === "Escape") closeStopModal(false);
    if (e.key === "Enter") { e.preventDefault(); closeStopModal(true); }
    return;
  }
  if (phase === "preview") {
    if (e.key === "Enter" && document.activeElement !== els.question) {
      e.preventDefault(); els.approveBtn.click();
    } else if (e.key === "Escape") {
      e.preventDefault(); els.rejectBtn.click();
    }
  }
});

els.stopBtn.addEventListener("click", () => {
  if (phase === "generating" || phase === "executing") openStopModal();
});

function openStopModal() {
  lastFocus = document.activeElement;
  show(els.backdrop, true);
  els.confirmStop.focus();
}
function closeStopModal(doStop) {
  show(els.backdrop, false);
  if (doStop && runId) {
    fetch("/api/stop", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ run_id: runId }),
    });
  }
  if (lastFocus) lastFocus.focus();
}
els.confirmStop.addEventListener("click", () => closeStopModal(true));
els.cancelStop.addEventListener("click", () => closeStopModal(false));
els.backdrop.addEventListener("mousedown", (e) => {
  if (e.target === els.backdrop) closeStopModal(false);
});

pollStatus();
