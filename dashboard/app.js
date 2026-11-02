const POLL_INTERVAL_MS = 3000;

const LABELS = {
  sku: {
    in_sync: "Synchron",
    failed: "Fehler",
    blocked: "Blockiert",
    missing_in_shop: "Fehlt im Shop",
    unmapped: "Nur im Shop",
  },
  run: {
    running: "Läuft",
    succeeded: "Erfolgreich",
    partial: "Teilweise",
    failed: "Fehlgeschlagen",
    blocked: "Blockiert",
    aborted: "Abgebrochen",
  },
  action: {
    updated: "Aktualisiert",
    planned: "Geplant",
    failed: "Fehler",
    blocked: "Blockiert",
    missing_in_shop: "Fehlt im Shop",
    unmapped: "Nur im Shop",
  },
  mode: { incremental: "Inkrementell", full: "Vollabgleich" },
  trigger: { manual: "manuell", schedule: "automatisch" },
};

const TONES = {
  in_sync: "ok",
  succeeded: "ok",
  updated: "ok",
  failed: "danger",
  partial: "warn",
  blocked: "warn",
  aborted: "warn",
  missing_in_shop: "info",
  unmapped: "info",
  planned: "accent",
  running: "accent",
};

const state = {
  overview: null,
  inventory: [],
  runs: [],
  events: [],
  error: null,
  busy: false,
};

const $ = (selector) => document.querySelector(selector);

const elements = {
  schedulerStatus: $("#scheduler-status"),
  toggleScheduler: $("#toggle-scheduler"),
  cadence: $("#cadence"),
  banner: $("#banner"),
  statSync: $("#stat-sync"),
  statSyncHint: $("#stat-sync-hint"),
  statProblems: $("#stat-problems"),
  statUnmatched: $("#stat-unmatched"),
  statRuns: $("#stat-runs"),
  statRunsHint: $("#stat-runs-hint"),
  statRetries: $("#stat-retries"),
  inventory: $("#inventory"),
  inventoryMeta: $("#inventory-meta"),
  runs: $("#runs"),
  events: $("#events"),
  chaosForm: $("#chaos-form"),
  chaosReset: $("#chaos-reset"),
  failureRateOutput: $("#failure-rate-output"),
  toast: $("#toast"),
  statusBar: $("#status-bar"),
  statusLegend: $("#status-legend"),
  stockChart: $("#stock-chart"),
  runsChart: $("#runs-chart"),
  chartTooltip: $("#chart-tooltip"),
  runButtons: [...document.querySelectorAll("[data-mode]")],
};

// ---------- API ----------

class ApiError extends Error {
  constructor(status, message) {
    super(message);
    this.status = status;
  }
}

async function api(url, options = {}) {
  let response;
  try {
    response = await fetch(url, {
      headers: { "Content-Type": "application/json" },
      ...options,
      body: options.body === undefined ? undefined : JSON.stringify(options.body),
    });
  } catch {
    throw new ApiError(0, "Sync-Service nicht erreichbar");
  }
  if (response.ok) {
    return response.json();
  }

  let message = response.statusText;
  try {
    const body = await response.json();
    if (typeof body.detail === "string") {
      message = body.detail;
    } else if (response.status === 422) {
      message = "Ungültige Eingabe";
    }
  } catch {
    // Body is not JSON, keep the status text.
  }
  throw new ApiError(response.status, message);
}

async function refresh() {
  try {
    const [overview, inventory, runs, events] = await Promise.all([
      api("/api/overview"),
      api("/api/inventory"),
      api("/api/runs?limit=20"),
      api("/api/events?limit=30"),
    ]);
    Object.assign(state, { overview, inventory, runs, events, error: null });
  } catch (error) {
    state.error = error;
  }
  render();
}

// ---------- Formatting ----------

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#39;");
}

function badge(kind, value) {
  const label = LABELS[kind][value] ?? value;
  return `<span class="badge ${TONES[value] ?? ""}">${escapeHtml(label)}</span>`;
}

function timeAgo(iso) {
  if (!iso) return "–";
  const seconds = Math.max(0, Math.round((Date.now() - new Date(iso).getTime()) / 1000));
  if (seconds < 5) return "gerade eben";
  if (seconds < 60) return `vor ${seconds} s`;
  const minutes = Math.round(seconds / 60);
  if (minutes < 60) return `vor ${minutes} min`;
  const hours = Math.round(minutes / 60);
  if (hours < 24) return `vor ${hours} h`;
  return new Date(iso).toLocaleString("de-DE");
}

function formatDuration(ms) {
  if (ms === null || ms === undefined) return "–";
  return ms < 1000 ? `${ms} ms` : `${(ms / 1000).toFixed(1)} s`;
}

function formatQuantity(value) {
  return value === null || value === undefined ? "–" : String(value);
}

// ---------- Rendering ----------

function render() {
  renderHeader();
  renderBanner();
  renderStats();
  renderCharts();
  renderInventory();
  renderRuns();
  renderEvents();
}

function renderCharts() {
  if (!state.overview) return;
  renderStatusBar(elements.statusBar, elements.statusLegend, state.overview);
  renderStockComparison(elements.stockChart, state.inventory);
  renderRunsChart(elements.runsChart, state.runs);
}

function renderHeader() {
  const scheduler = state.overview?.scheduler;
  const pill = elements.schedulerStatus;

  if (state.error && !state.overview) {
    pill.textContent = "Offline";
    pill.className = "pill danger";
    return;
  }
  if (!scheduler) return;

  if (!scheduler.enabled) {
    pill.textContent = "Automatik deaktiviert";
    pill.className = "pill";
  } else if (scheduler.paused) {
    pill.textContent = "Automatik pausiert";
    pill.className = "pill warn";
  } else {
    pill.textContent = `Automatik aktiv · geprüft ${timeAgo(scheduler.last_check_at)}`;
    pill.className = "pill ok";
  }

  elements.toggleScheduler.hidden = !scheduler.enabled;
  elements.toggleScheduler.textContent = scheduler.paused ? "Fortsetzen" : "Pausieren";
  elements.cadence.textContent =
    `Inkrementeller Sync alle ${scheduler.incremental_interval_seconds} s, ` +
    `Vollabgleich alle ${scheduler.full_interval_seconds} s. Das ERP ist die führende Quelle.`;

  document.querySelectorAll(".demo-only").forEach((element) => {
    element.hidden = !state.overview.demo_mode;
  });
}

function renderBanner() {
  const lastRun = state.overview?.last_run;
  let message = null;
  let tone = "";
  let action = "";

  if (state.error) {
    message = `${state.error.message}. Angezeigt wird der zuletzt geladene Stand.`;
  } else if (lastRun?.status === "blocked") {
    message = `Lauf #${lastRun.id} wurde blockiert: ${lastRun.error}`;
    tone = "warn";
    action =
      '<button class="button small" type="button" id="override-guard">Prüfen & trotzdem übertragen</button>';
  } else if (lastRun?.status === "failed") {
    message = `Lauf #${lastRun.id} ist fehlgeschlagen: ${lastRun.error ?? "siehe Aktivität"}`;
  }

  elements.banner.hidden = message === null;
  elements.banner.className = `banner ${tone}`;
  elements.banner.innerHTML = message ? `<span>${escapeHtml(message)}</span>${action}` : "";
}

function renderStats() {
  const overview = state.overview;
  if (!overview) return;
  const counts = overview.sku_counts;
  const count = (key) => counts[key] ?? 0;

  elements.statSync.textContent = `${count("in_sync")} / ${overview.total_skus}`;
  elements.statSyncHint.textContent =
    overview.total_skus === 0 ? "noch kein Abgleich" : "SKUs mit gleichem Bestand";
  elements.statProblems.textContent = count("failed") + count("blocked");
  elements.statUnmatched.textContent = count("missing_in_shop") + count("unmapped");
  elements.statRuns.textContent = overview.runs_24h;
  elements.statRunsHint.textContent =
    overview.failed_runs_24h > 0
      ? `${overview.failed_runs_24h} mit Fehlern`
      : "alle ohne Fehler";
  elements.statRetries.textContent = overview.retries_24h;
}

function renderInventory() {
  // Do not replace the table while someone is typing a new stock value.
  if (elements.inventory.contains(document.activeElement)) return;

  const rows = state.inventory;
  elements.inventoryMeta.textContent = `${rows.length} SKUs`;
  if (rows.length === 0) {
    elements.inventory.innerHTML =
      '<tr><td colspan="7" class="empty">Noch kein Abgleich – starte einen Vollabgleich.</td></tr>';
    return;
  }

  const demoMode = state.overview?.demo_mode;
  elements.inventory.innerHTML = rows
    .map((row) => {
      const sku = escapeHtml(row.sku);
      const mismatch =
        row.erp_quantity !== null &&
        row.shop_quantity !== null &&
        row.erp_quantity !== row.shop_quantity;
      const editor =
        row.erp_quantity === null
          ? '<span class="muted">–</span>'
          : `<form class="stock-form" data-sku="${sku}">
              <input type="number" name="quantity" min="0" step="1" value="${row.erp_quantity}"
                aria-label="Neuer ERP-Bestand für ${sku}" required>
              <button class="button small secondary" type="submit">Speichern</button>
            </form>`;
      const error = row.last_error
        ? `<span class="error-text">${escapeHtml(row.last_error)}</span>`
        : "";

      return `<tr>
        <td><code>${sku}</code></td>
        <td>${escapeHtml(row.name)}</td>
        <td class="num">${formatQuantity(row.erp_quantity)}</td>
        <td class="num ${mismatch ? "mismatch" : ""}">${formatQuantity(row.shop_quantity)}</td>
        <td>${badge("sku", row.status)}${error}</td>
        <td class="muted">${timeAgo(row.last_synced_at)}</td>
        ${demoMode ? `<td>${editor}</td>` : ""}
      </tr>`;
    })
    .join("");
}

function renderRuns() {
  if (state.runs.length === 0) {
    elements.runs.innerHTML = '<tr><td colspan="7" class="empty">Noch keine Läufe.</td></tr>';
    return;
  }
  elements.runs.innerHTML = state.runs
    .slice(0, 8)
    .map(
      (run) => `<tr>
        <td class="muted">${run.id}</td>
        <td class="run-kind">${LABELS.mode[run.mode] ?? run.mode}${run.dry_run ? '<span class="tag">Dry-Run</span>' : ""}
          <span class="muted">· ${LABELS.trigger[run.trigger] ?? run.trigger}</span></td>
        <td>${badge("run", run.status)}</td>
        <td class="num">${run.changed}</td>
        <td class="num">${run.failed}</td>
        <td class="num">${formatDuration(run.duration_ms)}</td>
        <td class="muted">${timeAgo(run.started_at)}</td>
      </tr>`,
    )
    .join("");
}

function renderEvents() {
  if (state.events.length === 0) {
    elements.events.innerHTML = '<li class="empty">Noch keine Aktivität.</li>';
    return;
  }
  elements.events.innerHTML = state.events
    .map((event) => {
      const change =
        event.to_quantity === null
          ? ""
          : ` <span class="muted">${formatQuantity(event.from_quantity)} → ${event.to_quantity}</span>`;
      const details = [`Lauf #${event.run_id}`];
      if (event.attempts > 0) {
        details.push(`${event.attempts} ${event.attempts === 1 ? "Versuch" : "Versuche"}`);
      }
      if (event.duration_ms > 0) details.push(formatDuration(event.duration_ms));
      if (event.error) details.push(escapeHtml(event.error));

      return `<li>
        ${badge("action", event.action)}
        <span><code>${escapeHtml(event.sku)}</code>${change}</span>
        <span class="feed-time">${timeAgo(event.created_at)}</span>
        <span class="feed-meta">${details.join(" · ")}</span>
      </li>`;
    })
    .join("");
}

// ---------- Actions ----------

let toastTimer;

function showToast(message, variant = "") {
  elements.toast.textContent = message;
  elements.toast.className = `toast ${variant}`;
  elements.toast.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => {
    elements.toast.hidden = true;
  }, 4000);
}

function describeRun(run) {
  if (run.dry_run) {
    return `Dry-Run: ${run.changed} Änderungen geplant, nichts geschrieben.`;
  }
  const label = LABELS.run[run.status] ?? run.status;
  return `Lauf #${run.id} ${label.toLowerCase()}: ${run.changed} geändert, ${run.failed} Fehler, ${formatDuration(run.duration_ms)}.`;
}

async function startRun(button) {
  if (state.busy) return;
  state.busy = true;
  const originalText = button.textContent;
  elements.runButtons.forEach((other) => {
    other.disabled = true;
  });
  button.textContent = "Läuft …";

  try {
    const run = await api("/api/runs", {
      method: "POST",
      body: { mode: button.dataset.mode, dry_run: button.dataset.dryRun === "true" },
    });
    const failed = ["failed", "blocked", "partial"].includes(run.status);
    showToast(describeRun(run), failed ? "error" : "");
  } catch (error) {
    showToast(
      error.status === 409 ? "Es läuft bereits ein Sync – gleich noch einmal versuchen." : error.message,
      "error",
    );
  } finally {
    state.busy = false;
    button.textContent = originalText;
    elements.runButtons.forEach((other) => {
      other.disabled = false;
    });
    await refresh();
  }
}

async function changeErpStock(form) {
  const sku = form.dataset.sku;
  const quantity = Number(form.elements.quantity.value);
  const button = form.querySelector("button");
  button.disabled = true;

  try {
    await api(`/api/demo/erp/${encodeURIComponent(sku)}`, { method: "PATCH", body: { quantity } });
    showToast(`ERP: ${sku} auf ${quantity} gesetzt. Der nächste Sync überträgt die Änderung.`);
    document.activeElement?.blur();
    await refresh();
  } catch (error) {
    showToast(`ERP: ${error.message}`, "error");
  } finally {
    button.disabled = false;
  }
}

function fillChaosForm(settings) {
  const form = elements.chaosForm;
  form.elements.failure_rate.value = Math.round(settings.failure_rate * 100);
  form.elements.latency_ms.value = settings.latency_ms;
  form.elements.rate_limit_requests.value = settings.rate_limit_requests;
  updateFailureRateOutput();
}

function updateFailureRateOutput() {
  elements.failureRateOutput.textContent = `${elements.chaosForm.elements.failure_rate.value} %`;
}

async function applyChaos(settings) {
  try {
    const applied = await api("/api/demo/chaos", { method: "PUT", body: settings });
    fillChaosForm(applied);
    const active = applied.failure_rate > 0 || applied.latency_ms > 0;
    showToast(active ? "Störungen aktiv – starte jetzt einen Sync." : "Shop läuft wieder normal.");
  } catch (error) {
    showToast(`Störungen: ${error.message}`, "error");
  }
}

async function loadChaos() {
  try {
    fillChaosForm(await api("/api/demo/chaos"));
  } catch {
    // The panel keeps its defaults when the mock systems are not reachable.
  }
}

elements.runButtons.forEach((button) => {
  button.addEventListener("click", () => startRun(button));
});

elements.toggleScheduler.addEventListener("click", async () => {
  const paused = !state.overview?.scheduler.paused;
  try {
    await api("/api/scheduler", { method: "PUT", body: { paused } });
    showToast(paused ? "Automatik pausiert." : "Automatik läuft wieder.");
  } catch (error) {
    showToast(error.message, "error");
  }
  await refresh();
});

elements.banner.addEventListener("click", async (event) => {
  if (event.target.id !== "override-guard") return;
  const confirmed = window.confirm(
    "Wirklich übertragen? Die betroffenen Produkte werden im Shop auf 0 gesetzt " +
      "und sind dann nicht mehr bestellbar. Nur bestätigen, wenn die ERP-Daten stimmen.",
  );
  if (!confirmed) return;

  try {
    const run = await api("/api/runs", {
      method: "POST",
      body: { mode: "full", override_guard: true },
    });
    showToast(describeRun(run), run.status === "succeeded" ? "" : "error");
  } catch (error) {
    showToast(error.message, "error");
  }
  await refresh();
});

elements.inventory.addEventListener("submit", (event) => {
  event.preventDefault();
  changeErpStock(event.target);
});

elements.chaosForm.elements.failure_rate.addEventListener("input", updateFailureRateOutput);

elements.chaosForm.addEventListener("submit", (event) => {
  event.preventDefault();
  const form = elements.chaosForm.elements;
  applyChaos({
    failure_rate: Number(form.failure_rate.value) / 100,
    latency_ms: Number(form.latency_ms.value),
    rate_limit_requests: Number(form.rate_limit_requests.value),
  });
});

elements.chaosReset.addEventListener("click", () => {
  applyChaos({ failure_rate: 0, latency_ms: 0, rate_limit_requests: 10 });
});

let resizeTimer;
window.addEventListener("resize", () => {
  clearTimeout(resizeTimer);
  resizeTimer = setTimeout(() => {
    resetChartCache();
    renderCharts();
  }, 150);
});

setupChartTooltip(elements.chartTooltip);

setInterval(() => {
  if (!document.hidden && !state.busy) refresh();
}, POLL_INTERVAL_MS);

refresh();
loadChaos();
