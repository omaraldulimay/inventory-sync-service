// Small SVG/HTML charts without a library. Each chart is a pure function of its data,
// so a poll that returns the same data does not redraw anything.

const STATUS_SEGMENTS = [
  { key: "in_sync", color: "var(--status-good)", ink: "#ffffff" },
  { key: "missing_in_shop", color: "var(--status-serious)", ink: "#ffffff" },
  { key: "unmapped", color: "var(--status-neutral)", ink: "#ffffff" },
  { key: "blocked", color: "var(--status-warning)", ink: "#0b0b0b" },
  { key: "failed", color: "var(--status-critical)", ink: "#ffffff" },
];

const chartCache = new Map();

function drawOnce(container, data, draw) {
  const key = JSON.stringify([data, container.clientWidth]);
  if (chartCache.get(container) === key) return;
  chartCache.set(container, key);
  draw();
}

function niceScale(maxValue, minimumMax = 4) {
  const target = Math.max(maxValue, minimumMax);
  const rough = target / 4;
  const magnitude = 10 ** Math.floor(Math.log10(rough));
  const step = [1, 2, 5, 10].map((factor) => factor * magnitude).find((value) => value >= rough);
  return { max: Math.ceil(target / step) * step, step };
}

// Rectangle with rounded data end on top and a square edge on the baseline.
function columnPath(x, y, width, height) {
  const r = Math.min(4, height / 2, width / 2);
  return `M${x},${y + height}V${y + r}Q${x},${y} ${x + r},${y}H${x + width - r}Q${x + width},${y} ${x + width},${y + r}V${y + height}Z`;
}

function tip(lines) {
  return escapeHtml(lines.join("\n"));
}

// ---------- 1. Segmented bar: share of SKUs per status ----------

function renderStatusBar(bar, legend, overview) {
  const counts = overview.sku_counts;
  const total = overview.total_skus;
  drawOnce(bar, counts, () => {
    const parts = STATUS_SEGMENTS.filter((segment) => (counts[segment.key] ?? 0) > 0);
    if (total === 0) {
      bar.innerHTML = '<div class="chart-empty">Noch kein Abgleich.</div>';
      legend.innerHTML = "";
      bar.removeAttribute("aria-label");
      return;
    }

    const width = bar.clientWidth;
    bar.innerHTML = parts
      .map((segment) => {
        const count = counts[segment.key];
        const share = count / total;
        const label = LABELS.sku[segment.key];
        const percent = `${Math.round(share * 100)} %`;
        const fits = share * width >= 52;
        return `<div class="segment" tabindex="0"
            style="flex: ${count} 1 0; background: ${segment.color}; color: ${segment.ink}"
            data-tip="${tip([label, `${count} von ${total} SKUs · ${percent}`])}">
            ${fits ? `<span>${percent}</span>` : ""}
          </div>`;
      })
      .join("");
    bar.setAttribute(
      "aria-label",
      parts.map((s) => `${LABELS.sku[s.key]}: ${counts[s.key]} von ${total}`).join(", "),
    );
    legend.innerHTML = parts
      .map(
        (segment) =>
          `<span class="legend-item"><span class="key" style="--key: ${segment.color}"></span>${LABELS.sku[segment.key]}</span>`,
      )
      .join("");
  });
}

// ---------- 2. Dumbbell: ERP vs. shop stock per SKU ----------

function renderStockComparison(container, inventory) {
  const rows = inventory.filter((row) => row.erp_quantity !== null || row.shop_quantity !== null);
  drawOnce(container, rows, () => {
    if (rows.length === 0) {
      container.innerHTML = '<div class="chart-empty">Noch keine Bestände.</div>';
      return;
    }

    const width = Math.max(container.clientWidth, 280);
    const labelWidth = width < 480 ? 112 : 140;
    const right = 44;
    const top = 6;
    const rowHeight = 26;
    const plotBottom = top + rows.length * rowHeight;
    const height = plotBottom + 24;
    const values = rows.flatMap((row) => [row.erp_quantity ?? 0, row.shop_quantity ?? 0]);
    const { max, step } = niceScale(Math.max(...values));
    const x = (value) => labelWidth + (value / max) * (width - labelWidth - right);

    const grid = [];
    for (let value = 0; value <= max; value += step) {
      grid.push(`<line class="grid" x1="${x(value)}" x2="${x(value)}" y1="${top}" y2="${plotBottom}"/>
        <text class="axis" x="${x(value)}" y="${plotBottom + 16}" text-anchor="middle">${value}</text>`);
    }

    const marks = rows.map((row, index) => {
      const y = top + index * rowHeight + rowHeight / 2;
      const erp = row.erp_quantity;
      const shop = row.shop_quantity;
      const both = erp !== null && shop !== null;
      const same = both && erp === shop;
      const parts = [
        `<text class="row-label" x="${labelWidth - 10}" y="${y + 4}" text-anchor="end">${escapeHtml(row.sku)}</text>`,
      ];
      if (both && !same) {
        parts.push(`<line class="connector" x1="${x(erp)}" x2="${x(shop)}" y1="${y}" y2="${y}"/>`);
      }
      if (shop !== null) {
        parts.push(`<circle class="dot" cx="${x(shop)}" cy="${y}" r="${same ? 7 : 5}" fill="var(--series-2)"/>`);
      }
      if (erp !== null) {
        parts.push(`<circle class="dot" cx="${x(erp)}" cy="${y}" r="${same ? 4 : 5}" fill="var(--series-1)"/>`);
      }
      if (both && !same) {
        parts.push(`<text class="delta" x="${width - right + 8}" y="${y + 4}">${formatDiff(shop - erp)}</text>`);
      }

      const lines = [
        `${row.sku} · ${row.name ?? ""}`,
        `ERP: ${formatQuantity(erp)} · Shop: ${formatQuantity(shop)}`,
        LABELS.sku[row.status] ?? row.status,
      ];
      parts.push(
        `<rect class="hit" x="0" y="${y - rowHeight / 2}" width="${width}" height="${rowHeight}" data-tip="${tip(lines)}"/>`,
      );
      return `<g>${parts.join("")}</g>`;
    });

    container.innerHTML = `<svg width="${width}" height="${height}" viewBox="0 0 ${width} ${height}"
        role="img" aria-label="ERP- und Shop-Bestand je SKU">${grid.join("")}${marks.join("")}</svg>`;
  });
}

function formatDiff(value) {
  if (value > 0) return `+${value}`;
  if (value < 0) return `−${Math.abs(value)}`;
  return "0";
}

// ---------- 3. Columns: changes and failures per run ----------

function renderRunsChart(container, runs) {
  const items = [...runs].reverse();
  drawOnce(container, items, () => {
    if (items.length === 0) {
      container.innerHTML = '<div class="chart-empty">Noch keine Läufe.</div>';
      return;
    }

    const width = Math.max(container.clientWidth, 280);
    const height = 320;
    const margin = { top: 10, right: 8, bottom: 24, left: 34 };
    const plotWidth = width - margin.left - margin.right;
    const plotHeight = height - margin.top - margin.bottom;
    const baseline = margin.top + plotHeight;
    const { max, step } = niceScale(Math.max(...items.map((run) => run.changed + run.failed)));
    const scaleY = (value) => (value / max) * plotHeight;
    const band = plotWidth / items.length;
    const barWidth = Math.min(24, band * 0.6);
    const labelEvery = Math.ceil(items.length / Math.max(1, Math.floor(plotWidth / 36)));

    const grid = [];
    for (let value = 0; value <= max; value += step) {
      const y = baseline - scaleY(value);
      grid.push(`<line class="${value === 0 ? "baseline" : "grid"}" x1="${margin.left}" x2="${width - margin.right}" y1="${y}" y2="${y}"/>
        <text class="axis" x="${margin.left - 8}" y="${y + 4}" text-anchor="end">${value}</text>`);
    }

    const columns = items.map((run, index) => {
      const x = margin.left + index * band + (band - barWidth) / 2;
      const changedHeight = scaleY(run.changed);
      const failedHeight = scaleY(run.failed);
      const parts = [];

      if (run.changed + run.failed === 0) {
        parts.push(`<rect class="stub" x="${x}" y="${baseline - 2}" width="${barWidth}" height="2"/>`);
      }
      if (changedHeight > 0) {
        parts.push(`<path d="${columnPath(x, baseline - changedHeight, barWidth, changedHeight)}" fill="var(--series-1)"/>`);
      }
      if (failedHeight > 0) {
        const gap = changedHeight > 0 ? 2 : 0;
        const y = baseline - changedHeight - gap - failedHeight;
        parts.push(`<path d="${columnPath(x, y, barWidth, failedHeight)}" fill="var(--status-critical)"/>`);
      }
      // Count from the newest run so the latest one is always labelled and labels never collide.
      if ((items.length - 1 - index) % labelEvery === 0) {
        parts.push(`<text class="axis" x="${x + barWidth / 2}" y="${baseline + 16}" text-anchor="middle">#${run.id}</text>`);
      }

      const lines = [
        `Lauf #${run.id} · ${LABELS.mode[run.mode] ?? run.mode}${run.dry_run ? " (Dry-Run)" : ""}`,
        `${LABELS.run[run.status] ?? run.status} · ${LABELS.trigger[run.trigger] ?? run.trigger}`,
        `${run.changed} ${run.dry_run ? "geplant" : "geändert"} · ${run.failed} Fehler · ${run.retries} Retries`,
        `${formatDuration(run.duration_ms)} · ${timeAgo(run.started_at)}`,
      ];
      parts.push(
        `<rect class="hit" x="${margin.left + index * band}" y="${margin.top}" width="${band}" height="${plotHeight}" data-tip="${tip(lines)}"/>`,
      );
      return `<g>${parts.join("")}</g>`;
    });

    container.innerHTML = `<svg width="${width}" height="${height}" viewBox="0 0 ${width} ${height}"
        role="img" aria-label="Änderungen und Fehler je Lauf">${grid.join("")}${columns.join("")}</svg>`;
  });
}

// ---------- Tooltip shared by all charts ----------

function setupChartTooltip(tooltip) {
  const show = (target, clientX, clientY) => {
    tooltip.textContent = target.dataset.tip;
    tooltip.hidden = false;
    const { width, height } = tooltip.getBoundingClientRect();
    const left = Math.min(clientX + 14, window.innerWidth - width - 8);
    const top = clientY + 14 + height > window.innerHeight ? clientY - height - 14 : clientY + 14;
    tooltip.style.transform = `translate(${Math.max(8, left)}px, ${Math.max(8, top)}px)`;
  };

  document.addEventListener("mousemove", (event) => {
    const target = event.target.closest?.("[data-tip]");
    if (target) {
      show(target, event.clientX, event.clientY);
    } else {
      tooltip.hidden = true;
    }
  });
  document.addEventListener("focusin", (event) => {
    const target = event.target.closest?.("[data-tip]");
    if (!target) return;
    const rect = target.getBoundingClientRect();
    show(target, rect.left + rect.width / 2, rect.bottom);
  });
  document.addEventListener("focusout", () => {
    tooltip.hidden = true;
  });
}

function resetChartCache() {
  chartCache.clear();
}
