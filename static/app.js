"use strict";

const $ = (s) => document.querySelector(s);
const form = $("#search"), tickerEl = $("#ticker"), yearsEl = $("#years");
const modeEl = $("#mode"), modeHintEl = $("#mode-hint");
const goEl = $("#go"), xlsxEl = $("#xlsx"), statusEl = $("#status"), outEl = $("#out");

// Row groupings mirror the Financial Data sheet.
const IS_ROWS = ["Revenue (Net Sales)", "Cost of Revenue (COGS)", "Gross Profit",
  "Operating Expenses", "Operating Income (EBIT)", "Interest Expense",
  "Pretax Income", "Income Tax Expense", "Net Income"];
const ASSET_ROWS = ["Cash & Short-Term Investments", "Accounts Receivable", "Inventory",
  "Other Current Assets", "Total Current Assets", "Property, Plant & Equipment (net)",
  "Goodwill & Intangible Assets", "Other Long-Term Assets", "Total Assets"];
const LIAB_ROWS = ["Accounts Payable", "Short-Term Debt & Current Portion of LTD",
  "Other Current Liabilities", "Total Current Liabilities", "Long-Term Debt",
  "Other Long-Term Liabilities", "Total Liabilities", "Total Shareholders' Equity"];
// Not part of any subtotal. Gross PP&E is the Fixed Asset Turnover denominator.
const MEMO_ROWS = ["Property, Plant & Equipment (gross)", "Accumulated Depreciation"];
const TOTALS = new Set(["Gross Profit", "Operating Income (EBIT)", "Net Income",
  "Total Current Assets", "Total Assets", "Total Current Liabilities",
  "Total Liabilities", "Total Shareholders' Equity"]);

const money = (v) => v === null || v === undefined ? null :
  (v / 1e6).toLocaleString("en-US", { maximumFractionDigits: 1, minimumFractionDigits: 1 });
const fmt = (v, kind) => {
  if (v === null || v === undefined || !isFinite(v)) return null;
  if (kind === "%") return (v * 100).toFixed(1) + "%";
  if (kind === "x") return v.toFixed(2) + "×";
  return v.toLocaleString("en-US", { maximumFractionDigits: 2 });
};
const el = (tag, cls, text) => {
  const n = document.createElement(tag);
  if (cls) n.className = cls;
  if (text !== undefined) n.textContent = text;
  return n;
};

function table(years, rows, valueFmt, cls) {
  const t = el("table", cls);
  const thead = el("thead"), htr = el("tr");
  htr.appendChild(el("th", null, ""));
  years.forEach((y) => htr.appendChild(el("th", null, "FY" + y)));
  thead.appendChild(htr); t.appendChild(thead);

  const tb = el("tbody");
  rows.forEach((r) => {
    if (r.band) {
      const tr = el("tr", "band");
      const td = el("td", null, r.band);
      td.colSpan = years.length + 1;
      tr.appendChild(td); tb.appendChild(tr); return;
    }
    const tr = el("tr", r.cls || (TOTALS.has(r.label) ? "total" : ""));
    tr.appendChild(el("td", null, r.label));
    (r.values || []).forEach((v) => {
      const s = valueFmt(v, r.format);
      const td = el("td", s === null ? "num blank" : "num", s === null ? "—" : s);
      tr.appendChild(td);
    });
    tb.appendChild(tr);
  });
  t.appendChild(tb);
  return t;
}

// Most rows draw on the same XBRL tag every year, so five identical 70-character
// cells is mostly noise -- and at phone width it forced the table to ~3,500px,
// or shredded the names one character per line once they were made to wrap.
// Collapse a uniform row into a single spanning cell and only break a row out
// per year when the tags actually differ, which is the case worth looking at.
function provenanceTable(years, provenance) {
  const t = el("table", "tags");
  const thead = el("thead"), htr = el("tr");
  htr.appendChild(el("th", null, "Row"));
  years.forEach((y) => htr.appendChild(el("th", null, "FY" + y)));
  thead.appendChild(htr);
  t.appendChild(thead);

  const tb = el("tbody");
  Object.entries(provenance).forEach(([label, tags]) => {
    const shown = tags.map((x) => x || "not reported");
    const tr = el("tr");
    tr.appendChild(el("td", null, label));

    // Collapse consecutive years that share a tag into one spanning cell. A row
    // that never changed becomes a single wide cell; one that changed in FY2023
    // becomes two. Either way each cell is wide enough to wrap at word-ish
    // boundaries instead of one character per line.
    let i = 0;
    const runs = [];
    while (i < shown.length) {
      let j = i;
      while (j + 1 < shown.length && shown[j + 1] === shown[i]) j++;
      runs.push({ tag: shown[i], span: j - i + 1 });
      i = j + 1;
    }
    runs.forEach((run) => {
      const cls = run.tag === "not reported" ? "num blank"
                : runs.length > 1 ? "num changed" : "num tag-all";
      const td = el("td", cls, run.tag);
      td.colSpan = run.span;
      if (runs.length > 1 && run.tag !== "not reported") {
        td.title = run.span === 1
          ? "FY" + years[shown.indexOf(run.tag)]
          : "spans " + run.span + " years";
      }
      tr.appendChild(td);
    });
    tb.appendChild(tr);
  });
  t.appendChild(tb);
  return t;
}

function section(title, note, node) {
  const s = el("section");
  s.appendChild(el("h2", null, title));
  if (note) s.appendChild(el("p", "note", note));
  if (node) s.appendChild(node);
  return s;
}

function statementRows(labels, statements) {
  return labels.map((l) => ({ label: l, values: statements[l] || [] }));
}

function render(d) {
  outEl.replaceChildren();
  const years = d.fiscal_years;
  const diag = d.diagnostics;

  // Header
  const head = el("section");
  head.appendChild(el("h2", null, `${d.name} (${d.ticker})`));
  const meta = el("div", "meta");
  const balPill = diag.reconciles ? ["ok", "Balance sheet reconciles"]
    : ["bad", "Balance sheet does NOT reconcile"];
  // Data mode talks about line items, not ratios. Naming a ratio the reader is
  // supposed to derive themselves gives part of the exercise away.
  const dataMode = d.mode === "data";
  const complete = diag.ratio_inputs_present === diag.ratio_inputs_total;
  const ratioPill = complete
    ? ["ok", dataMode ? "All line items present"
                      : `All ${d.ratios.length} ratios computable`]
    : ["warn", `${diag.ratio_inputs_present}/${diag.ratio_inputs_total} ` +
               (dataMode ? "line items present" : "ratio inputs present")];
  [balPill, ratioPill].forEach(([c, txt]) => {
    const w = el("div"); w.appendChild(el("span", "pill " + c, txt)); meta.appendChild(w);
  });
  meta.appendChild(el("div", null, `CIK ${d.cik}`));
  meta.appendChild(el("div", null, `FY${years[0]}–FY${years[years.length - 1]}`));
  head.appendChild(meta);

  if (!diag.suitable) {
    head.appendChild(el("p", "note", "⚠ " + diag.suitability_note));
  }
  if (d.mezzanine_note) head.appendChild(el("p", "note", d.mezzanine_note));
  const blocked = Object.entries(diag.blocked_ratios || {});
  if (blocked.length) {
    head.appendChild(el("p", "note", "Not reported by this filer: " +
      (dataMode ? blocked.map(([k]) => k).join("; ")
                : blocked.map(([k, v]) => `${k} (blocks ${v})`).join("; "))));
  }
  outEl.appendChild(head);

  // Market
  const q = d.quote;
  const kpis = el("div", "kpis");
  const addKpi = (label, value) => {
    const k = el("div", "kpi");
    k.appendChild(el("b", null, value === null || value === undefined ? "—" : value));
    k.appendChild(el("span", null, label));
    kpis.appendChild(k);
  };

  if (d.mode === "data") {
    // Inputs only. Price and share count are facts; every multiple built from
    // them is a calculation, which is the reader's job in this mode.
    addKpi("Price", q.price === null ? null : "$" + q.price.toFixed(2));
    addKpi("Shares outstanding",
      d.shares_outstanding ? (d.shares_outstanding / 1e6).toLocaleString("en-US",
        { maximumFractionDigits: 1 }) + "M" : null);
    const dnote = (q.price === null
      ? "Price unavailable (" + (q.error || "no quote") + ")."
      : `Price from ${q.source}, delayed.`) +
      ` Shares outstanding as of ${d.shares_asof} (10-Q/10-K cover page).` +
      " Market ratios are not computed in this mode.";
    outEl.appendChild(section("Market inputs", dnote, kpis));
    renderStatements(d, years);
    renderSources(d, years);
    outEl.hidden = false;
    return;
  }

  const m = d.market;
  addKpi("Price", m.price === null ? null : "$" + m.price.toFixed(2));
  addKpi("Market cap", m.market_cap === null ? null : "$" + (m.market_cap / 1e9).toFixed(1) + "B");
  addKpi("EPS (diluted, FY" + years[years.length - 1] + ")",
    m.eps[m.eps.length - 1] === null ? null : "$" + m.eps[m.eps.length - 1].toFixed(2));
  addKpi("P/E", m.pe === null ? null : fmt(m.pe, "n"));
  addKpi("Market / Book", fmt(m.market_to_book, "n"));
  addKpi("Book value / share",
    m.book_value_per_share === null ? null : "$" + m.book_value_per_share.toFixed(2));

  let mnote;
  if (q.price === null) {
    mnote = "Price unavailable (" + (q.error || "no quote") +
      "). EDGAR-sourced ratios below are unaffected.";
  } else {
    mnote = `Price from ${q.source}, delayed. Shares outstanding ` +
      `${(m.shares_outstanding || 0).toLocaleString()} as of ${d.shares_asof} ` +
      `(10-Q/10-K cover page).`;
    if (m.eps_basis) mnote += ` EPS basis: ${m.eps_basis}.`;
    if (m.pe_note) mnote += ` P/E ${m.pe_note}.`;
  }
  if (m.split_adjusted) {
    const list = m.splits.filter((s) => s.date > (d.period_ends[0] || ""))
      .map((s) => `${s.factor}:1 on ${s.date}`).join(", ");
    mnote += ` EPS restated onto today's share basis for ${list}.`;
  } else if (m.split_lookup_ok === false) {
    mnote += " Split history unavailable, so EPS is as-filed — a split inside" +
      " the window would break the trend.";
  }
  outEl.appendChild(section("Market", mnote, kpis));

  renderStatements(d, years);

  // Ratios
  const rrows = [];
  let group = null;
  d.ratios.forEach((r) => {
    if (r.group !== group) { group = r.group; rrows.push({ band: group }); }
    rrows.push(r);
  });
  outEl.appendChild(section("Ratios", "Period-end balances, matching the downloadable workbook.",
    table(years, rrows, fmt)));

  // DuPont
  const du = d.dupont;
  const duRows = du.rows.slice();
  duRows.push({ label: "CHECK: difference", values: du.check, format: "n", cls: "check" });
  outEl.appendChild(section("DuPont decomposition",
    du.ok ? "ROE = Net Profit Margin × Total Asset Turnover × Equity Multiplier. Check row is zero."
          : "⚠ Check row is not zero — a component is missing or inconsistent.",
    table(years, duRows, fmt)));

  renderSources(d, years);
  outEl.hidden = false;
}

// The reported statements. Both modes render these; only full mode goes further.
function renderStatements(d, years) {
  outEl.appendChild(section("Income statement", "US$ millions, as filed.",
    table(years, statementRows(IS_ROWS, d.statements), money)));
  outEl.appendChild(section("Balance sheet", "US$ millions, as filed.",
    table(years, [{ band: "Assets" }, ...statementRows(ASSET_ROWS, d.statements),
      { band: "Liabilities & equity" }, ...statementRows(LIAB_ROWS, d.statements),
      { band: "Memo — not part of the subtotals above" },
      ...statementRows(MEMO_ROWS, d.statements)], money)));
}

// Sources is an appendix and goes LAST in both modes. Folding it into
// renderStatements put it between the balance sheet and the ratios, which
// buried the analysis under a very long provenance table -- it read as the end
// of the page.
function renderSources(d, years) {
  outEl.appendChild(section("Where each number came from",
    "One XBRL tag per year. A row drawing on more than one tag means the filer changed presentation mid-window — expected, not an error.",
    provenanceTable(years, d.provenance)));
}

async function run(ticker, years, mode) {
  mode = mode || modeEl.value;
  statusEl.className = "";
  statusEl.textContent = `Pulling ${ticker.toUpperCase()} from EDGAR…`;
  outEl.hidden = true;
  xlsxEl.hidden = true;
  goEl.disabled = true;
  const qs = `years=${years}&mode=${encodeURIComponent(mode)}`;
  try {
    const r = await fetch(`/api/company/${encodeURIComponent(ticker)}?${qs}`);
    const body = await r.json();
    if (!r.ok) throw new Error(body.detail || `HTTP ${r.status}`);
    render(body);
    statusEl.textContent = "";
    xlsxEl.href = `/api/company/${encodeURIComponent(ticker)}/xlsx?${qs}`;
    xlsxEl.textContent = mode === "data"
      ? "Download .xlsx (data only)" : "Download .xlsx";
    xlsxEl.hidden = false;
  } catch (err) {
    statusEl.className = "error";
    statusEl.textContent = err.message;
  } finally {
    goEl.disabled = false;
  }
}

form.addEventListener("submit", (e) => {
  e.preventDefault();
  const t = tickerEl.value.trim();
  if (t) run(t, yearsEl.value, modeEl.value);
});

modeEl.addEventListener("change", () => {
  modeHintEl.hidden = modeEl.value !== "data";
  const t = tickerEl.value.trim();
  if (t && !outEl.hidden) run(t, yearsEl.value, modeEl.value);
});

fetch("/api/companies").then((r) => r.json()).then((groups) => {
  const body = $("#picker-body");
  body.replaceChildren();
  Object.entries(groups).forEach(([sector, list]) => {
    body.appendChild(el("div", "group-label", sector));
    list.forEach((c) => {
      const b = el("button", "chip", c.ticker);
      b.type = "button";
      b.title = c.name;
      b.addEventListener("click", () => {
        tickerEl.value = c.ticker;
        $("#picker").open = false;
        run(c.ticker, yearsEl.value, modeEl.value);
      });
      body.appendChild(b);
    });
  });
}).catch(() => { $("#picker-body").textContent = "Screened list unavailable."; });
