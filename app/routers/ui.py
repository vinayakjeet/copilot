from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter
from fastapi.responses import HTMLResponse

router = APIRouter()

STATIC_DIR = Path(__file__).resolve().parents[1] / "static"

PAGE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Copilot, ask the warehouse</title>
<link rel="stylesheet" href="/static/style.css">
</head>
<body>
<main>
  <header class="top">
    <h1>Warehouse analyst</h1>
    <p class="sub">Ask in plain English. Every query is shown to you in full
    before it runs. Nothing here can write.</p>
    <p id="conn" class="meta" role="status"></p>
  </header>

  <form id="ask-form" aria-label="Ask the warehouse">
    <label for="question">Question</label>
    <div class="row">
      <input id="question" name="question" type="text" autocomplete="off"
             placeholder="Which cities had the most cancelled orders last quarter?" />
      <button id="ask-btn" type="submit">Ask</button>
      <button id="stop-btn" type="button" class="secondary hidden">Stop</button>
    </div>
  </form>

  <section id="coldstart" class="card hidden" aria-live="polite"
           aria-label="Service warming up">
    <h2>Warming up</h2>
    <ul id="stages" class="stages"></ul>
    <p class="meta">Free-tier hosting sleeps when idle; these are the real
    startup steps, not a spinner.</p>
  </section>

  <section id="sqlcard" class="card hidden" aria-label="Generated SQL">
    <div class="head">
      <h2>Proposed SQL</h2>
      <span id="attempt-badge" class="badge hidden">attempt 2</span>
    </div>
    <pre id="sql-preview" tabindex="0" class="sql"></pre>
    <p id="guard-note" class="meta"></p>
    <div class="actions">
      <button id="approve-btn" type="button">Run query</button>
      <button id="reject-btn" type="button" class="secondary">Reject</button>
      <span class="hint meta">Enter runs it, Esc rejects</span>
    </div>
  </section>

  <section id="resultcard" class="card hidden" aria-label="Results">
    <div class="head">
      <h2>Results</h2>
      <span id="stats" class="meta"></span>
    </div>
    <p id="live" class="visually-hidden" role="status" aria-live="polite"></p>
    <div id="chartbox"></div>
    <div id="tablebox" class="wrap"></div>
  </section>

  <section id="errorcard" class="card bad hidden" role="alert">
    <h2>Problem</h2>
    <p id="errormsg"></p>
  </section>

  <footer class="meta foot">
    Read-only role &middot; statement timeout &middot; row cap &middot;
    every run previewed before execution.
  </footer>
</main>

<div id="modal-backdrop" class="backdrop hidden">
  <div id="modal" class="modal" role="dialog" aria-modal="true"
       aria-labelledby="modal-title" aria-describedby="modal-desc">
    <h2 id="modal-title">Stop this run?</h2>
    <p id="modal-desc" class="sub">The server cancels the running query, not
    just the display.</p>
    <div class="actions">
      <button id="confirm-stop" type="button">Stop</button>
      <button id="cancel-stop" type="button" class="secondary">Keep going</button>
    </div>
  </div>
</div>

<script src="/static/app.js"></script>
</body>
</html>"""


@router.get("/", response_class=HTMLResponse)
async def index():
    return PAGE
