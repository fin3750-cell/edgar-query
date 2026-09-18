# edgar-query

Type a ticker, get five years of standardized financial statements, fourteen
ratios, the DuPont decomposition, and market multiples — pulled live from SEC
EDGAR's XBRL API. Download the whole thing as an Excel workbook with the
formulas intact.

Built for FIN 2010 at Utah Tech. The output matches the Module 05 Financial
Analysis template, so the app and the spreadsheet agree line for line.

## Run it locally

```bash
pip install -r requirements.txt
export SEC_CONTACT="Your Name, your.email@example.com"
uvicorn app:app --reload
```

Then open <http://127.0.0.1:8000>.

`SEC_CONTACT` is not optional. SEC's fair-access policy requires a real name and
email in the User-Agent header; requests without one get throttled or blocked.
It is read from the environment and never committed — this repo is public.

## How it works

`edgar/pull.py` fetches `companyfacts` for the ticker's CIK and resolves each
template row against a priority list of US-GAAP tags.

The important detail is that the tag list is walked **per year, not per row**.
Companies change tags mid-decade when an accounting standard changes. Target
reported `PropertyPlantAndEquipmentNet` through FY2022 and the
finance-lease-inclusive tag from FY2023 on — neither covers five years, but
together they do. Resolving one winning tag for the whole row drops it; resolving
year by year keeps it, and the Sources tab records which tag fed which year.

Other things it handles:

- **Fiscal year alignment.** Retailers close FY2023 in Jan/Feb 2024. A period
  ending in the first half of a calendar year belongs to the prior fiscal year.
- **Restatements.** Later filings restate earlier figures; the earliest original
  report wins, so the numbers match what was filed at the time.
- **Mezzanine equity.** Temporary equity is folded into Total Equity, as
  commercial data providers do, and the adjustment is reported rather than
  buried.
- **Unclassified balance sheets.** Banks, insurers, and firms with captive
  finance arms have no current-asset subtotal. The app says so instead of
  rendering broken liquidity ratios.
- **Derivations.** Gross Profit, Total Liabilities, EBIT and combined
  goodwill/intangibles are computed when not tagged directly, and marked
  `(derived)` in the Sources tab.

## Market data

EDGAR has no share prices. Price comes from Yahoo Finance's chart endpoint —
free, no key, unofficial. It fails soft: if the quote is unavailable, market
ratios blank out and every EDGAR-sourced ratio still works.

EPS prefers the filer's own reported diluted figure, falls back to Net Income
over a weighted-average share count, then to the cover-page count. Which basis
was used is always stated on screen. Dual-class filers like Hershey tag EPS per
share class, and `companyfacts` carries only undimensioned facts, so the reported
figure is genuinely missing for some perfectly ordinary companies.

P/E is suppressed when the company lost money — a negative multiple is
arithmetic, not information.

## Known limits

- **EPS is not split-adjusted.** Figures are as-filed, and original filings are
  not restated for later splits. NVDA's EPS series jumps around because of the
  4:1 and 10:1 splits, not because earnings did.
- **Yahoo throttles cloud IPs** harder than residential ones. Quotes are cached
  15 minutes; on a free Render instance expect occasional blanks.
- **Five years needs three filings.** A single 10-K carries two years of balance
  sheet data. The XBRL API aggregates across filings, which is why this works at
  all.
- `Operating Expenses` is frequently untagged. It feeds no ratio, so it is left
  blank rather than guessed at.

## Screened companies

`data/approved_companies.json` holds 33 companies across 8 sectors, screened for
a classified balance sheet, five years of 10-K data, and positive net income in
at least four of five years. The picker uses it; the search box accepts any
ticker.

## Deploying to Render

Push to GitHub, then create a Web Service pointed at the repo. `render.yaml`
has the build and start commands. Set `SEC_CONTACT` in the Render dashboard —
it is marked `sync: false` so it never lands in git.

The free tier sleeps after 15 minutes idle; the first request after that takes
roughly 50 seconds to wake.

## Layout

```
app.py              FastAPI routes
edgar/tags.py       US-GAAP tag priority lists
edgar/pull.py       companyfacts extraction, per-year tag resolution
edgar/market.py     Yahoo quote, fails soft
edgar/ratios.py     the 14 ratios, DuPont, market multiples
edgar/workbook.py   fills the Excel template, adds a Sources tab
static/             single-page frontend, no build step
data/template.xlsx  Module 05 template, formulas intact
```
