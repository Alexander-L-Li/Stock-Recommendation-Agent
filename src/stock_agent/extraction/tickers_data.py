"""Reference data for ticker extraction.

``KNOWN_TICKERS`` is a curated allowlist of commonly discussed US tickers. It is
intentionally not exhaustive — its job is to validate bare all-caps tokens so we
don't treat words like "CEO" or "USA" as tickers. Cashtags ($AAPL) bypass this
list. The allowlist can be extended/overridden at call time.

``STOPWORDS`` is a denylist of all-caps tokens that look like tickers but almost
never are in this context (acronyms, common abbreviations). Even if such a token
were a real ticker, excluding it favors precision, which matters more than recall
for a long-term, fundamentals-first agent.
"""
from __future__ import annotations

# Curated set of frequently discussed tickers across r/stocks, r/investing, etc.
KNOWN_TICKERS: frozenset[str] = frozenset(
    {
        # Mega/large-cap tech
        "AAPL", "MSFT", "GOOG", "GOOGL", "AMZN", "META", "NVDA", "TSLA", "NFLX",
        "AMD", "INTC", "CRM", "ORCL", "ADBE", "CSCO", "AVGO", "QCOM", "TXN",
        "IBM", "MU", "AMAT", "NOW", "PANW", "SNOW", "PLTR", "SHOP", "UBER",
        "ABNB", "SQ", "PYPL", "ARM", "SMCI", "DELL", "HPQ",
        # Semiconductors / hardware
        "TSM", "ASML", "LRCX", "KLAC", "ON", "MRVL", "WDC", "STX",
        # Financials
        "JPM", "BAC", "WFC", "C", "GS", "MS", "BLK", "SCHW", "AXP", "V", "MA",
        "BRK.B", "COF", "USB", "PNC", "BX", "KKR",
        # Healthcare / pharma
        "JNJ", "PFE", "MRK", "ABBV", "LLY", "UNH", "TMO", "ABT", "BMY", "AMGN",
        "GILD", "CVS", "MDT", "ISRG", "REGN", "VRTX",
        # Consumer
        "KO", "PEP", "PG", "WMT", "COST", "MCD", "SBUX", "NKE", "DIS", "HD",
        "LOW", "TGT", "CL", "MDLZ", "EL", "LULU", "CMG", "BKNG",
        # Energy / industrials
        "XOM", "CVX", "COP", "SLB", "OXY", "PSX", "MPC", "VLO", "ENPH", "FSLR",
        "BA", "CAT", "DE", "GE", "HON", "MMM", "LMT", "RTX", "UPS", "FDX",
        "UNP", "EMR", "ETN",
        # Communications / media / autos
        "T", "VZ", "TMUS", "CMCSA", "F", "GM", "RIVN", "LCID", "NIO",
        # Other widely discussed
        "BABA", "JD", "PDD", "SOFI", "HOOD", "COIN", "MARA", "RIOT", "GME",
        "AMC", "BB", "DKNG", "RBLX", "U", "DDOG", "NET", "CRWD", "ZS", "MDB",
        "TEAM", "WDAY", "ANET", "DOCU", "TWLO", "OKTA", "ROKU", "PINS", "SNAP",
        "SPOT", "ZM", "ETSY", "CVNA", "AFRM", "UPST",
        # Major ETFs (treated as candidates too)
        "SPY", "QQQ", "VOO", "VTI", "IWM", "DIA", "ARKK", "XLF", "XLE", "XLK",
    }
)

# All-caps tokens that resemble tickers but are noise in this context.
STOPWORDS: frozenset[str] = frozenset(
    {
        # Roles / business jargon
        "CEO", "CFO", "CTO", "COO", "IPO", "ETF", "ETFS", "EPS", "PE", "PEG",
        "ROE", "ROI", "ROIC", "EBIT", "EBITDA", "GDP", "CPI", "FED", "FOMC",
        "SEC", "FDA", "FTC", "IRS", "DOJ", "AI", "ML", "EV", "EVS", "USA",
        "US", "UK", "EU", "DD", "YOLO", "FOMO", "ATH", "ATL", "FUD", "TLDR",
        "TLDR", "IMO", "IMHO", "AKA", "FYI", "ASAP", "FAQ", "API", "SaaS",
        "GPU", "CPU", "TAM", "SAM", "ARR", "MRR", "YOY", "QOQ", "WSB", "OP",
        "EDIT", "USD", "EUR", "GBP", "JPY", "OK", "NO", "YES", "ALL", "FOR",
        "ARE", "THE", "AND", "BUT", "NOT", "CAN", "NEW", "NOW", "ONE", "TWO",
        "GET", "BUY", "SELL", "HOLD", "PUMP", "DUMP", "RH", "PR", "PSA",
        "Q1", "Q2", "Q3", "Q4", "FY", "TTM", "WTF", "LOL", "IRA", "401K",
    }
)
