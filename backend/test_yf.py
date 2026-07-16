import yfinance as yf
import pandas as pd

df_batch = yf.download("AAPL MSFT", period="1mo", interval="1d", auto_adjust=True)
df_single = yf.Ticker("SPY").history(period="1mo", interval="1d", auto_adjust=True)

print("Batch index tz:", df_batch.index.tz)
print("Single index tz:", df_single.index.tz)

rets_batch = df_batch["Close"]["AAPL"].pct_change().dropna()
rets_single = df_single["Close"].pct_change().dropna()

aligned = pd.concat([rets_batch, rets_single], axis=1, join="inner")
print("Aligned length:", len(aligned))
