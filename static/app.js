"use strict";

const $ = (s) => document.querySelector(s);
const form = $("#search"), tickerEl = $("#ticker"), yearsEl = $("#years");
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

function table(years, rows, valueFmt) {
  const t = el("table");
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
  const ratioPill = diag.ratio_inputs_present === diag.ratio_inputs_total
    ? ["ok", "All 14 ratios computable"]
    : ["warn", `${diag.ratio_inputs_present}/${diag.ratio_inputs_total} ratio inputs present`];
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
      blocked.map(([k, v]) => `${k} (blocks ${v})`).join("; ")));
  }
  outEl.appendChild(head);

  // Market
  const m = d.market, q = d.quote;
  const kpis = el("div", "kpis");
  const addKpi = (label, value) => {
    const k = el("div", "kpi");
    k.appendChild(el("b", null, value === null || value === undefined ? "—" : value));
    k.appendChild(el("span", null, label));
    kpis.appendChild(k);
  };
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
  outEl.appendChild(section("Market", mnote, kpis));

  // Statements
  outEl.appendChild(section("Income statement", "US$ millions, as filed.",
    table(years, statementRows(IS_ROWS, d.statements), money)));
  outEl.appendChild(section("Balance sheet", "US$ millions, as filed.",
    table(years, [{ band: "Assets" }, ...statementRows(ASSET_ROWS, d.statements),
      { band: "Liabilities & equity" }, ...statementRows(LIAB_ROWS, d.statements)], money)));

  // Ratios
  const rrows = [];
  let group = null;
  d.ratios.forEach((r) => {
    if (r.group !== group) { group = r.group; rrows.push({ band: group }); }
    rrows.push(r);
  });
  outEl.appendChild(section("Ratios", "Period-end balances, matching the Module 05 template.",
    table(years, rrows, fmt)));

  // DuPont
  const du = d.dupont;
  const duRows = du.rows.slice();
  duRows.push({ label: "CHECK: difference", values: du.check, format: "n", cls: "check" });
  outEl.appendChild(section("DuPont decomposition",
    du.ok ? "ROE = Net Profit Margin × Total Asset Turnover × Equity Multiplier. Check row is zero."
          : "⚠ Check row is not zero — a component is missing or inconsistent.",
    table(years, duRows, fmt)));

  // Provenance
  const provRows = Object.entries(d.provenance).map(([label, tags]) => ({
    label, values: tags, format: "tag"
  }));
  const provTable = table(years, provRows, (v) => v === null ? null : v);
  outEl.appendChild(section("Where each number came from",
    "One XBRL tag per year. A row drawing on more than one tag means the filer changed presentation mid-window — expected, not an error.",
    provTable));

  outEl.hidden = false;
}

async function run(ticker, years) {
  statusEl.className = "";
  statusEl.textContent = `Pulling ${ticker.toUpperCase()} from EDGAR…`;
  outEl.hidden = true;
  xlsxEl.hidden = true;
  goEl.disabled = true;
  try {
    const r = await fetch(`/api/company/${encodeURIComponent(ticker)}?years=${years}`);
    const body = await r.json();
    if (!r.ok) throw new Error(body.detail || `HTTP ${r.status}`);
    render(body);
    statusEl.textContent = "";
    xlsxEl.href = `/api/company/${encodeURIComponent(ticker)}/xlsx?years=${years}`;
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
  if (t) run(t, yearsEl.value);
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
        run(c.ticker, yearsEl.value);
      });
      body.appendChild(b);
    });
  });
}).catch(() => { $("#picker-body").textContent = "Screened list unavailable."; });
