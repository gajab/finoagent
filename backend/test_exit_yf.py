import yfinance as yf
import pandas as pd

raw = yf.download("XLK SPY", period="1y", interval="1d", progress=False, auto_adjust=True)
print("Columns:", raw.columns)
close = raw["Close"]
print("Is close a DataFrame?", isinstance(close, pd.DataFrame))
if isinstance(close, pd.DataFrame):
    print("Close columns:", close.columns)
    try:
        sector_close = close["XLK"].dropna()
        spy_close = close["SPY"].dropna()
        print("sector_close len:", len(sector_close))
        print("spy_close len:", len(spy_close))
    except Exception as e:
        print("Error:", e)
