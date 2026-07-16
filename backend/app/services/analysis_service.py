"""Analysis service — technical indicators and statistical price predictions.

Uses numpy/scipy/pandas on yfinance data. No deep learning.
"""

import asyncio
import math
from datetime import datetime, timedelta

import numpy as np
import pandas as pd
import yfinance as yf
from scipy import stats


# ---------------------------------------------------------------------------
# Technical Analysis
# ---------------------------------------------------------------------------

def _run_technical_analysis_sync(ticker: str, period_days: int = 90) -> dict:
    """Compute technical indicators for a ticker."""
    end = datetime.now()
    # Fetch extra history for long-term moving averages
    start = end - timedelta(days=max(period_days, 250))
    stock = yf.Ticker(ticker)
    df = stock.history(start=start.strftime("%Y-%m-%d"), end=end.strftime("%Y-%m-%d"))

    if df.empty or len(df) < 20:
        return {"error": f"Insufficient data for {ticker}", "ticker": ticker}

    close = df["Close"].values
    high = df["High"].values
    low = df["Low"].values
    volume = df["Volume"].values

    result: dict = {"ticker": ticker, "period_days": period_days, "data_points": len(df)}

    # Current price
    result["current_price"] = round(float(close[-1]), 2)

    # --- MACD (12, 26, 9) ---
    if len(close) >= 26:
        ema12 = pd.Series(close).ewm(span=12, adjust=False).mean().values
        ema26 = pd.Series(close).ewm(span=26, adjust=False).mean().values
        macd_line = ema12 - ema26
        signal_line = pd.Series(macd_line).ewm(span=9, adjust=False).mean().values
        histogram = macd_line - signal_line

        result["macd"] = {
            "macd_line": round(float(macd_line[-1]), 4),
            "signal_line": round(float(signal_line[-1]), 4),
            "histogram": round(float(histogram[-1]), 4),
            "signal": "bullish" if macd_line[-1] > signal_line[-1] else "bearish",
            "crossover": "bullish_crossover" if (macd_line[-2] <= signal_line[-2] and macd_line[-1] > signal_line[-1])
                         else "bearish_crossover" if (macd_line[-2] >= signal_line[-2] and macd_line[-1] < signal_line[-1])
                         else "none",
        }

    # --- Bollinger Bands (20, 2) ---
    if len(close) >= 20:
        sma20 = pd.Series(close).rolling(window=20).mean().values
        std20 = pd.Series(close).rolling(window=20).std().values
        upper_band = sma20[-1] + 2 * std20[-1]
        lower_band = sma20[-1] - 2 * std20[-1]
        band_width = upper_band - lower_band
        pct_b = (close[-1] - lower_band) / band_width if band_width > 0 else 0.5

        result["bollinger_bands"] = {
            "upper": round(float(upper_band), 2),
            "middle": round(float(sma20[-1]), 2),
            "lower": round(float(lower_band), 2),
            "bandwidth_pct": round(float(band_width / sma20[-1] * 100), 2) if sma20[-1] > 0 else 0,
            "percent_b": round(float(pct_b), 4),
            "position": "overbought" if pct_b > 1.0 else "oversold" if pct_b < 0.0 else "within_bands",
        }

    # --- SMA 50 / SMA 200 ---
    if len(close) >= 50:
        sma50 = float(pd.Series(close).rolling(window=50).mean().values[-1])
        result["sma50"] = round(sma50, 2)
        result["price_vs_sma50"] = "above" if close[-1] > sma50 else "below"

    if len(close) >= 200:
        sma200 = float(pd.Series(close).rolling(window=200).mean().values[-1])
        result["sma200"] = round(sma200, 2)
        result["price_vs_sma200"] = "above" if close[-1] > sma200 else "below"

        if "sma50" in result:
            result["golden_death_cross"] = "golden_cross" if result["sma50"] > sma200 else "death_cross"

    # --- EMA 12 / EMA 26 ---
    if len(close) >= 26:
        result["ema12"] = round(float(ema12[-1]), 2)
        result["ema26"] = round(float(ema26[-1]), 2)
        result["ema_crossover"] = "bullish" if ema12[-1] > ema26[-1] else "bearish"

    # --- RSI (14) ---
    if len(close) >= 15:
        deltas = np.diff(close)
        gains = np.where(deltas > 0, deltas, 0)
        losses = np.where(deltas < 0, -deltas, 0)
        avg_gain = np.mean(gains[-14:])
        avg_loss = np.mean(losses[-14:])
        rs = avg_gain / avg_loss if avg_loss > 0 else 100
        rsi = 100 - (100 / (1 + rs))
        result["rsi"] = round(float(rsi), 2)
        result["rsi_signal"] = "overbought" if rsi > 70 else "oversold" if rsi < 30 else "neutral"

    # --- Volume analysis ---
    if len(volume) >= 20:
        avg_vol_recent = float(np.mean(volume[-5:]))
        avg_vol_20 = float(np.mean(volume[-20:]))
        result["volume"] = {
            "recent_avg_5d": int(avg_vol_recent),
            "avg_20d": int(avg_vol_20),
            "volume_trend": "increasing" if avg_vol_recent > avg_vol_20 * 1.2 else
                           "decreasing" if avg_vol_recent < avg_vol_20 * 0.8 else "stable",
        }

    # --- Support / Resistance (simplified) ---
    recent = close[-min(60, len(close)):]
    result["support"] = round(float(np.min(recent)), 2)
    result["resistance"] = round(float(np.max(recent)), 2)

    # --- Signal summary ---
    signals = []
    if "macd" in result:
        signals.append(f"MACD: {result['macd']['signal']}")
    if "bollinger_bands" in result:
        signals.append(f"Bollinger: {result['bollinger_bands']['position']}")
    if "rsi" in result:
        signals.append(f"RSI({result['rsi']}): {result['rsi_signal']}")
    if "golden_death_cross" in result:
        signals.append(f"MA Cross: {result['golden_death_cross'].replace('_', ' ')}")
    result["signal_summary"] = "; ".join(signals)

    return result


async def run_technical_analysis(ticker: str, period_days: int = 90) -> dict:
    """Async wrapper for technical analysis."""
    return await asyncio.to_thread(_run_technical_analysis_sync, ticker, period_days)


# ---------------------------------------------------------------------------
# Price Prediction (Statistical)
# ---------------------------------------------------------------------------

def _run_price_prediction_sync(ticker: str, horizon_days: int = 30) -> dict:
    """Simple statistical price prediction — no deep learning."""
    end = datetime.now()
    start = end - timedelta(days=365)
    stock = yf.Ticker(ticker)
    df = stock.history(start=start.strftime("%Y-%m-%d"), end=end.strftime("%Y-%m-%d"))

    if df.empty or len(df) < 30:
        return {"error": f"Insufficient data for {ticker}", "ticker": ticker}

    close = df["Close"].values
    current_price = float(close[-1])

    result: dict = {
        "ticker": ticker,
        "current_price": round(current_price, 2),
        "horizon_days": horizon_days,
        "data_points": len(df),
    }

    # --- Linear Regression Trend ---
    x = np.arange(len(close))
    slope, intercept, r_value, p_value, std_err = stats.linregress(x, close)
    projected_price = intercept + slope * (len(close) + horizon_days)
    daily_change_pct = slope / current_price * 100

    result["linear_trend"] = {
        "slope_per_day": round(float(slope), 4),
        "daily_change_pct": round(float(daily_change_pct), 4),
        "r_squared": round(float(r_value ** 2), 4),
        "projected_price": round(float(projected_price), 2),
        "projected_change_pct": round(float((projected_price - current_price) / current_price * 100), 2),
        "trend_direction": "upward" if slope > 0 else "downward",
        "trend_strength": "strong" if r_value ** 2 > 0.7 else "moderate" if r_value ** 2 > 0.3 else "weak",
    }

    # --- Mean Reversion Score ---
    sma50 = float(np.mean(close[-min(50, len(close)):]))
    std50 = float(np.std(close[-min(50, len(close)):]))
    z_score = (current_price - sma50) / std50 if std50 > 0 else 0

    result["mean_reversion"] = {
        "sma50": round(sma50, 2),
        "z_score": round(float(z_score), 4),
        "deviation_pct": round(float((current_price - sma50) / sma50 * 100), 2),
        "signal": "strongly_oversold" if z_score < -2 else
                  "oversold" if z_score < -1 else
                  "overbought" if z_score > 1 else
                  "strongly_overbought" if z_score > 2 else "neutral",
    }

    # --- Momentum Score (Rate of Change) ---
    roc_14 = (close[-1] - close[-min(14, len(close))]) / close[-min(14, len(close))] * 100
    roc_30 = (close[-1] - close[-min(30, len(close))]) / close[-min(30, len(close))] * 100 if len(close) >= 30 else None

    result["momentum"] = {
        "roc_14d_pct": round(float(roc_14), 2),
        "roc_30d_pct": round(float(roc_30), 2) if roc_30 is not None else None,
        "signal": "strong_bullish" if roc_14 > 10 else
                  "bullish" if roc_14 > 3 else
                  "bearish" if roc_14 < -3 else
                  "strong_bearish" if roc_14 < -10 else "neutral",
    }

    # --- Volatility Forecast ---
    returns = np.diff(close) / close[:-1]
    daily_vol = float(np.std(returns))
    annual_vol = daily_vol * math.sqrt(252)
    projected_range_low = current_price * (1 - daily_vol * math.sqrt(horizon_days) * 1.96)
    projected_range_high = current_price * (1 + daily_vol * math.sqrt(horizon_days) * 1.96)

    result["volatility"] = {
        "daily_volatility_pct": round(daily_vol * 100, 4),
        "annualized_volatility_pct": round(annual_vol * 100, 2),
        "projected_95_range": {
            "low": round(float(projected_range_low), 2),
            "high": round(float(projected_range_high), 2),
        },
    }

    # --- Overall Signal ---
    score = 0
    # Trend contribution
    if result["linear_trend"]["trend_direction"] == "upward":
        score += 1 if result["linear_trend"]["trend_strength"] != "weak" else 0.5
    else:
        score -= 1 if result["linear_trend"]["trend_strength"] != "weak" else 0.5
    # Mean reversion contribution
    if z_score < -1:
        score += 1  # Oversold = potential upside
    elif z_score > 1:
        score -= 1  # Overbought = potential downside
    # Momentum contribution
    if roc_14 > 3:
        score += 0.5
    elif roc_14 < -3:
        score -= 0.5

    if score >= 1.5:
        overall = "bullish"
        confidence = min(85, 50 + int(score * 15))
    elif score <= -1.5:
        overall = "bearish"
        confidence = min(85, 50 + int(abs(score) * 15))
    else:
        overall = "neutral"
        confidence = max(30, 50 - int(abs(score) * 10))

    result["overall_signal"] = {
        "signal": overall,
        "confidence_pct": confidence,
        "score": round(float(score), 2),
    }

    result["disclaimer"] = (
        "This is a statistical analysis based on historical data. "
        "It is not financial advice. Past performance does not guarantee future results."
    )

    return result


async def run_price_prediction(ticker: str, horizon_days: int = 30) -> dict:
    """Async wrapper for price prediction."""
    return await asyncio.to_thread(_run_price_prediction_sync, ticker, horizon_days)
