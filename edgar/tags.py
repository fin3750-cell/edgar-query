"""
US-GAAP tag priority lists, one per template row.

Companies tag the same concept differently, and a single company will change
tags mid-decade when an accounting standard changes. The resolver in pull.py
walks these lists PER YEAR, so a company that switched tags in FY2023 still
produces a complete five-year row.

  dur  = income statement (covers a period)
  inst = balance sheet (a point in time)
"""

MAP = [
    ("Revenue (Net Sales)", "dur", [
        "RevenueFromContractWithCustomerExcludingAssessedTax",
        "RevenueFromContractWithCustomerIncludingAssessedTax",
        "Revenues",
        "SalesRevenueNet",
        "SalesRevenueGoodsNet",
    ]),
    ("Cost of Revenue (COGS)", "dur", [
        "CostOfGoodsAndServicesSold",
        "CostOfRevenue",
        "CostOfGoodsSold",
        "CostOfSales",
        "CostOfGoodsAndServicesSoldExcludingDepreciationDepletionAndAmortization",
        "CostOfGoodsSoldExcludingDepreciationDepletionAndAmortization",
    ]),
    ("Gross Profit", "dur", ["GrossProfit"]),
    ("Operating Expenses", "dur", [
        "OperatingExpenses",
        "CostsAndExpenses",
        "OperatingCostsAndExpenses",
    ]),
    ("Operating Income (EBIT)", "dur", ["OperatingIncomeLoss"]),
    ("Interest Expense", "dur", [
        "InterestExpense",
        "InterestExpenseNonoperating",
        "InterestExpenseDebt",
        "InterestIncomeExpenseNet",
        "InterestAndDebtExpense",
    ]),
    ("Pretax Income", "dur", [
        "IncomeLossFromContinuingOperationsBeforeIncomeTaxesExtraordinaryItemsNoncontrollingInterest",
        "IncomeLossFromContinuingOperationsBeforeIncomeTaxesMinorityInterestAndIncomeLossFromEquityMethodInvestments",
        "IncomeLossFromContinuingOperationsBeforeIncomeTaxesDomestic",
    ]),
    ("Income Tax Expense", "dur", ["IncomeTaxExpenseBenefit"]),
    ("Net Income", "dur", ["NetIncomeLoss", "ProfitLoss"]),

    ("Cash & Short-Term Investments", "inst", [
        "CashCashEquivalentsAndShortTermInvestments",
        "CashAndCashEquivalentsAtCarryingValue",
        "CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents",
        "CashAndCashEquivalentsAtCarryingValueIncludingDiscontinuedOperations",
    ]),
    ("Accounts Receivable", "inst", [
        "AccountsReceivableNetCurrent",
        "ReceivablesNetCurrent",
        "AccountsAndOtherReceivablesNetCurrent",
        "AccountsReceivableGrossCurrent",
    ]),
    ("Inventory", "inst", [
        "InventoryNet",
        "InventoryFinishedGoodsNetOfReserves",
        "RetailRelatedInventoryMerchandise",
    ]),
    ("Other Current Assets", "inst", [
        "OtherAssetsCurrent",
        "PrepaidExpenseAndOtherAssetsCurrent",
    ]),
    ("Total Current Assets", "inst", ["AssetsCurrent"]),
    ("Property, Plant & Equipment (net)", "inst", [
        "PropertyPlantAndEquipmentNet",
        "PropertyPlantAndEquipmentAndFinanceLeaseRightOfUseAssetAfterAccumulatedDepreciationAndAmortization",
        "PropertyPlantAndEquipmentIncludingFinanceLeaseRightOfUseAssetNet",
    ]),
    # Memo rows. Not part of the balance sheet subtotals -- gross PP&E is the
    # denominator of Fixed Asset Turnover, which cares about the asset base a
    # company built, not what is left of it after depreciation.
    ("Property, Plant & Equipment (gross)", "inst", [
        "PropertyPlantAndEquipmentGross",
        "PropertyPlantAndEquipmentAndFinanceLeaseRightOfUseAssetBeforeAccumulatedDepreciationAndAmortization",
        "PropertyPlantAndEquipmentOther",
    ]),
    ("Accumulated Depreciation", "inst", [
        "AccumulatedDepreciationDepletionAndAmortizationPropertyPlantAndEquipment",
        "PropertyPlantAndEquipmentAndFinanceLeaseRightOfUseAssetAccumulatedDepreciationAndAmortization",
    ]),
    ("Goodwill & Intangible Assets", "inst", ["IntangibleAssetsNetIncludingGoodwill"]),
    ("Other Long-Term Assets", "inst", ["OtherAssetsNoncurrent"]),
    ("Total Assets", "inst", ["Assets"]),

    ("Accounts Payable", "inst", [
        "AccountsPayableCurrent",
        "AccountsPayableTradeCurrent",
        "AccountsPayableAndAccruedLiabilitiesCurrent",
    ]),
    ("Short-Term Debt & Current Portion of LTD", "inst", [
        "LongTermDebtCurrent",
        "DebtCurrent",
        "ShortTermBorrowings",
        "OtherShortTermBorrowings",
    ]),
    ("Other Current Liabilities", "inst", [
        "OtherLiabilitiesCurrent",
        "AccruedLiabilitiesCurrent",
        "OtherAccruedLiabilitiesCurrent",
        "AccruedLiabilitiesAndOtherLiabilitiesCurrent",
    ]),
    ("Total Current Liabilities", "inst", ["LiabilitiesCurrent"]),
    ("Long-Term Debt", "inst", [
        "LongTermDebtNoncurrent",
        "LongTermDebt",
    ]),
    ("Other Long-Term Liabilities", "inst", ["OtherLiabilitiesNoncurrent"]),
    ("Total Liabilities", "inst", ["Liabilities"]),
    ("Total Shareholders' Equity", "inst", [
        "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest",
        "StockholdersEquity",
    ]),
]

# Pulled for derivations and market ratios, not written as template rows.
EXTRA = [
    ("Goodwill", "inst"),
    ("IntangibleAssetsNetExcludingGoodwill", "inst"),
]

# EPS, best source first. Dual-class filers (Hershey, for one) stopped tagging
# these without dimensions -- they report per share class, and companyfacts
# carries only the undimensioned facts. So every one of these can come back
# empty for a perfectly ordinary company, and the share-count chain below is
# what rescues it.
EPS_TAGS = [
    ("EarningsPerShareDiluted", "dur"),
    ("EarningsPerShareBasicAndDiluted", "dur"),
    ("IncomeLossFromContinuingOperationsPerDilutedShare", "dur"),
]

# Denominator for EPS when no EPS tag resolves. Weighted-average diluted is the
# figure that matches the income statement period; the rest are approximations
# in descending order of honesty.
SHARE_TAGS = [
    ("WeightedAverageNumberOfDilutedSharesOutstanding", "dur"),
    ("WeightedAverageNumberOfShareOutstandingBasicAndDiluted", "dur"),
    ("WeightedAverageNumberOfSharesOutstandingBasic", "dur"),
    ("CommonStockSharesOutstanding", "inst"),
]

MEZZANINE = [
    "TemporaryEquityCarryingAmountIncludingPortionAttributableToNoncontrollingInterests",
    "TemporaryEquityCarryingAmountAttributableToParent",
    "RedeemableNoncontrollingInterestEquityCarryingAmount",
]

# Rows the ratio sheet actually consumes. A gap here blocks a ratio.
#
# Gross PP&E is deliberately NOT in this list. It was, while Fixed Asset
# Turnover existed; once that ratio went, a missing gross PP&E was still
# counted against the total and still reported as blocking "several ratios" --
# the default string, since BLOCKS had no entry for it either. It blocks
# nothing now. It is still pulled, still derived and still written as a memo
# row; it simply is not a ratio input, so do not add it back without a ratio
# that consumes it.
RATIO_INPUTS = [
    "Revenue (Net Sales)", "Cost of Revenue (COGS)", "Gross Profit",
    "Operating Income (EBIT)", "Interest Expense", "Net Income",
    "Cash & Short-Term Investments", "Inventory", "Total Current Assets",
    "Total Assets", "Total Current Liabilities", "Total Liabilities",
    "Total Shareholders' Equity",
]

# Memo rows: pulled and reported, but excluded from the balance-sheet display
# and from the "missing data" warnings, since they are not part of any subtotal.
MEMO_ROWS = ["Property, Plant & Equipment (gross)", "Accumulated Depreciation"]

BLOCKS = {
    "Interest Expense": "Times Interest Earned",
    "Gross Profit": "Gross Margin",
    "Cost of Revenue (COGS)": "Inventory Turnover",
    "Cash & Short-Term Investments": "Cash Ratio",
    "Inventory": "Quick Ratio, Inventory Turnover",
    "Operating Income (EBIT)": "Operating Margin, Times Interest Earned",
}
