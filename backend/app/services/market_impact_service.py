import asyncio
import json
import logging
from datetime import datetime, timedelta
import yfinance as yf
from .llm_service import call_llm
from .stock_service import safe_float

logger = logging.getLogger(__name__)

async def analyze_market_impact(
    event_text: str,
    duration_str: str,
    exact_datetime: str,
    openai_key: str
) -> dict:
    """
    LLM Contextual analysis of a market event mapped against historical pricing arrays.
    """
    if not event_text or len(event_text.strip()) < 5:
        return {"error": "Please provide a valid event description."}

    # 1. Structure the Prompt
    current_time_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    system_prompt = (
        f"You are an elite quantitative macro-economist and geopolitical strategist. "
        f"The current date and time is {current_time_str}. This is the exact present macro timeline. "
        "A user has provided an event (e.g., a tweet, a news article, or economic data release). "
        "Your task is to analyze this event deeply. Consider WHO is speaking (e.g., President, Fed Chair, CEO), "
        "WHAT historical precedents exist for this type of event, and HOW it interacts with the current macro environment. "
        "Based on this, identify exactly 3 of the MOST GRANULAR, specific micro-sectors or sub-industries impacted "
        "(e.g., prefer 'Solid State Batteries' over 'Energy', or 'Military Aviation' over 'Industrials' or 'Defense'). "
        "Then, for EACH specific sub-sector, identify exactly 2 specific stock tickers (total 6 stocks). "
        "CRITICAL INSTRUCTION: If the event explicitly mentions or surrounds a specific company, that company's exact ticker MUST be included in the top_stocks array. "
        "Also identify exactly 6 ETF or Mutual Fund tickers spanning the exposure (exactly 2 for each sub-sector). "
        "For each identified sector, stock, and ETF, classify the anticipated reaction sentiment based on the event context as exactly one of: 'positive' (green/bullish), 'negative' (red/bearish), 'neutral', or 'unknown'. "
        "You MUST return ONLY valid JSON matching this schema exactly:\n"
        "{\n"
        '  "historical_context": "Deep analysis of speaker authority, past precedents, and current macro conditions...",\n'
        '  "top_sectors": [{"name": "Granular Sub-Sector 1", "sentiment": "positive"}, {"name": "Granular Sub-Sector 2", "sentiment": "negative"}, ...],\n'
        '  "top_stocks": [{"ticker": "AAPL", "sentiment": "positive"}, {"ticker": "TSLA", "sentiment": "negative"}, ...], // Exactly 6 valid Yahoo Finance tickers\n'
        '  "top_etfs": [{"ticker": "SMH", "sentiment": "positive"}, {"ticker": "XLF", "sentiment": "neutral"}, ...]\n'
        "}"
    )

    user_prompt = f"EVENT TEXT:\n{event_text}\n\nAnalyze the primary beneficiaries and casualties of this event."

    try:
        # Structure the OpenAI messages array
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt}
        ]
        
        # call_llm is already async, just await it
        llm_response = await call_llm(
            api_key=openai_key,
            model="gpt-4o",
            messages=messages,
            max_tokens=1500,
            expect_json=True
        )
        
        # Strip potential markdown formatting
        clean_json = llm_response.strip()
        if clean_json.startswith("```json"):
            clean_json = clean_json[7:]
        if clean_json.endswith("```"):
            clean_json = clean_json[:-3]
            
        parsed_llm = json.loads(clean_json.strip())

    except Exception as e:
        logger.error(f"LLM Impact Analysis failed: {e}")
        return {"error": f"Failed to analyze event with LLM: {str(e)}. Please check your OpenAI API Key."}

    # Extract LLM data as objects
    top_stocks_objects = parsed_llm.get("top_stocks", [])[:6]
    top_etfs_objects = parsed_llm.get("top_etfs", [])[:6]
    historical_context = parsed_llm.get("historical_context", "Analysis unavailable.")
    top_sectors_objects = parsed_llm.get("top_sectors", [])
    
    # Flatten objects into strings and build sentiment maps
    top_stocks = [s.get("ticker", s) if isinstance(s, dict) else s for s in top_stocks_objects]
    top_etfs = [e.get("ticker", e) if isinstance(e, dict) else e for e in top_etfs_objects]
    top_sectors = [sec.get("name", sec) if isinstance(sec, dict) else sec for sec in top_sectors_objects]
    
    stock_sentiments = {s.get("ticker", s): s.get("sentiment", "unknown") for s in top_stocks_objects if isinstance(s, dict)}
    etf_sentiments = {e.get("ticker", e): e.get("sentiment", "unknown") for e in top_etfs_objects if isinstance(e, dict)}
    sector_sentiments = {sec.get("name", sec): sec.get("sentiment", "unknown") for sec in top_sectors_objects if isinstance(sec, dict)}

    # 2. Setup standard targets
    indices = ["SPY", "QQQ", "DIA", "IWM"]
    all_tickers = list(set(top_stocks + top_etfs + indices))
    
    # 3. Determine Time Bounds for yfinance
    # If explicit datetime is provided, we center the window around it.
    # If not, we use duration_str to look backwards from `now`.
    
    now = datetime.now()
    start_date = None
    end_date = None
    interval = "1d" # default safe interval
    
    period = None
    if exact_datetime:
        try:
             # Expected format from frontend: YYYY-MM-DDTHH:MM
             event_dt = datetime.strptime(exact_datetime, "%Y-%m-%dT%H:%M")
             
             days_ago = (now - event_dt).days
             
             # If exact datetime is within 7 days, we can do extremely tight 1-hour before/after window.
             if days_ago <= 7:
                 start_date = event_dt - timedelta(hours=2)
                 end_date = event_dt + timedelta(hours=2)
                 interval = "2m"
             # If within 60 days, pad +/- 1 day (using 1h interval).
             elif days_ago <= 60:
                 start_date = event_dt - timedelta(days=1)
                 end_date = event_dt + timedelta(days=1)
                 interval = "1h"
             # Otherwise, pad +/- 5 days natively.
             else:
                 start_date = event_dt - timedelta(days=5)
                 end_date = event_dt + timedelta(days=5)
                 interval = "1d"
                 
             if end_date > now:
                  end_date = now
                  
        except Exception:
             # Fallback if unparseable
             start_date = now - timedelta(days=30)
             end_date = now
             interval = "1d"
             
        # Enforce yfinance limitations purely
        if interval in ["1m", "2m", "5m", "15m", "30m", "1h", "90m"]:
             if (now - start_date).days > 60:
                 interval = "1d"
             if interval in ["1m", "2m", "5m"] and (now - start_date).days > 7:
                 interval = "1h"
                 
        if interval == "1d":
            start_fmt = start_date.strftime("%Y-%m-%d")
            end_fmt = end_date.strftime("%Y-%m-%d") if end_date != now else None
        else:
            start_fmt = start_date
            end_fmt = end_date
            
    else:
        # Relative mappings logic: Use trading periods instead of absolute timestamps to avoid off-hours blanks
        start_fmt = None
        end_fmt = None
        if not duration_str or duration_str == "today":
             period = "1d" # Fetches today's active trading window
             interval = "2m"
        elif duration_str == "1hr" or duration_str == "1 hr":
             period = "1d" # Fetches today's active trading window
             interval = "2m"
        elif duration_str == "1day" or duration_str == "1 day":
             period = "5d" # Last 5 active trading days
             interval = "5m"
        elif duration_str == "1week" or duration_str == "1 week":
             period = "1mo" # Last 1 trailing month
             interval = "1h"
        elif duration_str == "1month" or duration_str == "1 month":
             period = "3mo" # Last 3 months
             interval = "1d"
        else:
             period = "1mo"
             interval = "1h"

    # 4. Fetch the Data inBulk
    # Note: `group_by="ticker"` makes the resulting DataFrame structured by Top-level Ticker -> Sub-level OHLCV
    chart_data = {}
    
    def _fetch_hist():
         kwargs = {
             "tickers": all_tickers,
             "interval": interval,
             "group_by": "ticker",
             "threads": True,
         }
         if period:
             kwargs["period"] = period
         else:
             kwargs["start"] = start_fmt
             if end_fmt:
                 kwargs["end"] = end_fmt
         return yf.download(**kwargs)
         
    try:
         hist_df = await asyncio.to_thread(_fetch_hist)
    except Exception as e:
         logger.warning(f"Bulk explicit yfinance download failed: {e}, falling back to individual parsing")
         hist_df = None
         
    # 5. Format Data for the Frontend Charts
    # The frontend expects { "AAPL": { labels: [timestamps], prices: [floats], volumes: [ints] } }
    
    def process_ticker_df(ticker_symbol: str, df) -> dict:
        result = {"labels": [], "prices": [], "volumes": [], "opens": []}
        try:
             # If bulk download was successful and has multiple tickers, it's a multi-index column df
             if isinstance(df.columns, pd.MultiIndex):
                  if ticker_symbol in df.columns.levels[0]:
                       ticker_data = df[ticker_symbol].dropna()
                  else:
                       return result
             else:
                  # If we downloaded a single ticker, it's a flat dataframe
                  ticker_data = df.dropna()
                  
             if ticker_data.empty:
                  return result
                  
             for ts, row in ticker_data.iterrows():
                  if interval == "1d":
                       result["labels"].append(ts.strftime("%Y-%m-%d"))
                  else:
                       try:
                           # Make timezone aware if naive (yfinance uses NY time)
                           if getattr(ts, "tzinfo", None) is None:
                               ts = ts.tz_localize("America/New_York")
                       except Exception:
                           pass
                       result["labels"].append(ts.isoformat())
                  result["prices"].append(safe_float(row.get("Close", 0.0)))
                  result["opens"].append(safe_float(row.get("Open", 0.0)))
                  result["volumes"].append(int(safe_float(row.get("Volume", 0))))
        except Exception:
             pass
        return result

    # Standard loop to pack payload
    import pandas as pd
    
    if hist_df is not None and not hist_df.empty:
         for t in top_stocks:
             chart_data[t] = process_ticker_df(t, hist_df)
         for t in top_etfs:
             chart_data[t] = process_ticker_df(t, hist_df)
         for t in indices:
             chart_data[t] = process_ticker_df(t, hist_df)
    else:
         # Fallback to individual fetches
         for t in all_tickers:
              try:
                   tick_obj = yf.Ticker(t)
                   kwargs = {"interval": interval, "start": start_fmt}
                   if end_fmt: kwargs["end"] = end_fmt
                   df = tick_obj.history(**kwargs)
                   chart_data[t] = process_ticker_df(t, df)
              except Exception:
                   chart_data[t] = {"labels": [], "prices": [], "volumes": []}

    # 5.5 Try to append fast_info.previous_close for intraday baseline shifts
    if period == "1d" or duration_str == "today":
        try:
             tickers_obj = yf.Tickers(" ".join(all_tickers))
             for t in all_tickers:
                  try:
                       pc = getattr(tickers_obj.tickers[t].fast_info, "previous_close", None)
                       if pc is not None and t in chart_data:
                            chart_data[t]["previous_close"] = float(pc)
                  except Exception:
                       pass
        except Exception as e:
             logger.warning(f"Could not append previous_close: {e}")

    # 6. Second Pass LLM for Index Analysis
    index_performance_text = ""
    for idx in indices:
        c_data = chart_data.get(idx)
        if c_data and c_data.get("prices") and len(c_data["prices"]) > 0:
            start_price = c_data.get("previous_close") or c_data["prices"][0]
            end_price = c_data["prices"][-1]
            pct_change = ((end_price - start_price) / start_price) * 100
            index_performance_text += f"- {idx}: Moved from ${start_price:.2f} to ${end_price:.2f} ({pct_change:+.2f}%)\n"
        else:
            index_performance_text += f"- {idx}: Data unavailable for this window.\n"
            
    if index_performance_text.strip():
        second_sys_prompt = (
            "You are an elite quantitative macro analyst. "
            "You will be given a market event, and the ACTUAL empirical price performance of major indices during the window surrounding this event. "
            "Write a very concise (2-3 sentences max) 'Macro Index Reaction' summarizing how the broader market behaved and digested the event, "
            "based strictly on this actual price data. "
            "Additionally, assign a 'sentiment' evaluation based on the price action to each index indicating: 'positive', 'negative', 'neutral', or 'unknown'. "
            "Return ONLY valid JSON matching this schema exactly:\n"
            "{\n"
            '  "summary": "The broader market...",\n'
            '  "index_sentiments": {"SPY": "positive", "QQQ": "negative", "DIA": "neutral", "IWM": "unknown"}\n'
            "}"
        )
        second_usr_prompt = f"EVENT TEXT:\n{event_text}\n\nACTUAL INDEX PERFORMANCE:\n{index_performance_text}"
        
        try:
            second_messages = [
                {"role": "system", "content": second_sys_prompt},
                {"role": "user", "content": second_usr_prompt}
            ]
            second_llm_response = await call_llm(
                api_key=openai_key,
                model="gpt-4o",
                messages=second_messages,
                max_tokens=800,
                expect_json=True
            )
            
            clean_idx_json = second_llm_response.strip()
            if clean_idx_json.startswith("```json"):
                clean_idx_json = clean_idx_json[7:]
            if clean_idx_json.endswith("```"):
                clean_idx_json = clean_idx_json[:-3]
                
            parsed_idx = json.loads(clean_idx_json.strip())
            index_analysis = parsed_idx.get("summary", "Analysis unavailable.")
            index_sentiments = parsed_idx.get("index_sentiments", {})
            
        except Exception as e:
            logger.error(f"Second pass index analysis failed: {e}")
            index_analysis = "Empirical index analysis unavailable due to LLM error."
            index_sentiments = {}
    else:
        index_analysis = "Cannot compute index reaction (chart data unavailable)."
        index_sentiments = {}

    return {
         "success": True,
         "llm_analysis": {
              "historical_context": historical_context,
              "top_sectors": top_sectors,
              "index_analysis": index_analysis,
              "sentiments": {
                  "sectors": sector_sentiments,
                  "stocks": stock_sentiments,
                  "etfs": etf_sentiments,
                  "indices": index_sentiments
              }
         },
         "targets": {
              "stocks": top_stocks,
              "etfs": top_etfs,
              "indices": indices
         },
         "chart_data": chart_data,
         "metadata": {
              "interval_used": interval,
              "start_tracked": start_date.strftime("%Y-%m-%d %H:%M") if not period else f"Period window: {period}",
              "event_datetime": exact_datetime if exact_datetime else f"Relative {duration_str}"
         }
    }
