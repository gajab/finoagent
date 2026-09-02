"""Market-sentiment desk agent — recent NEWS + StockTwits crowd chatter distilled into an ACTIONABLE read
for a specific options-income trade, with an EARNINGS focus.

This is NOT part of the deterministic grade — it's the qualitative color a quant screen can't see (what the
crowd and headlines think, catalysts, and especially the earnings setup) to help the user build conviction
before placing a premium-selling trade. Best-effort: a missing source degrades gracefully.
"""
from __future__ import annotations

import json
import logging
from typing import Optional

from .llm_service import call_llm, clean_json_text
from . import social_service

logger = logging.getLogger(__name__)

# Which side an income seller is SHORT → which market direction threatens the trade (frames the whole read).
_SHORT_CALL = {"naked_call", "covered_call", "call_credit_spread"}
_SHORT_PUT = {"cash_secured_put", "put_credit_spread"}
_BOTH_SIDES = {"short_strangle", "iron_condor", "jade_lizard", "collar"}


def _threat_direction(structure: Optional[str]) -> str:
    if structure in _SHORT_CALL:
        return "a RALLY — the stock moving UP through your short call"
    if structure in _SHORT_PUT:
        return "a SELLOFF — the stock moving DOWN through your short put"
    if structure in _BOTH_SIDES:
        return "a big move EITHER way that breaks out of your range"
    return "a large directional move against the short leg"


def _fetch_news_headlines(ticker: str, limit: int = 12) -> list[dict]:
    """Recent headlines from yfinance. Handles both the new nested-`content` shape and the older flat one."""
    try:
        import yfinance as yf
        raw = yf.Ticker(ticker).news or []
    except Exception as exc:  # noqa: BLE001 — best-effort
        logger.info("sentiment: news fetch failed for %s: %s", ticker, exc)
        return []
    out: list[dict] = []
    for item in raw[:limit]:
        c = item.get("content") or item
        title = (c.get("title") or item.get("title") or "").strip()
        if not title:
            continue
        prov = ((c.get("provider") or {}).get("displayName") if isinstance(c.get("provider"), dict)
                else None) or item.get("publisher") or ""
        summ = (c.get("summary") or item.get("summary") or "")[:300]
        link = ((c.get("canonicalUrl") or {}).get("url") if isinstance(c.get("canonicalUrl"), dict)
                else None) or item.get("link") or ""
        out.append({"title": title, "publisher": prov, "summary": summ, "date": c.get("pubDate") or "", "link": link})
    return out


def _describe_trade(trade: dict) -> str:
    label = trade.get("label") or trade.get("structure") or "income trade"
    parts = [str(label)]
    if trade.get("short_strike"):
        parts.append(f"short strike ${trade['short_strike']}")
    if trade.get("expiration"):
        parts.append(f"expiring {trade['expiration']}")
    return " · ".join(parts)


_SYS = (
    "You are a markets-desk analyst. Distill recent NEWS and StockTwits CROWD chatter into a short, ACTIONABLE "
    "sentiment read for a specific options-INCOME trade that a premium seller is considering. You do NOT score "
    "or grade the trade — a separate quant desk does that. You provide the QUALITATIVE colour a mechanical "
    "screen misses: what the crowd and headlines are thinking, near-term catalysts, notable price levels, and — "
    "most important when a print is near — the EARNINGS setup (what people expect, the move they're pricing, the "
    "hype). Social media is noisy and frequently WRONG: separate signal from hype, flag rumour as rumour, and "
    "NEVER invent a fact that isn't in the material provided. Ground every point in the given messages/headlines.\n"
    "INDEPENDENCE (critical): this is a PURE crowd + news read — you have NOT been given, and must NOT cite, any "
    "model output (an 'expected move %', probability, fair value, grade or target). Any expected move, price "
    "target or earnings expectation you mention must come from what the NEWS or CROWD actually says, in THEIR "
    "own words/numbers. If the material doesn't quantify the move, describe it qualitatively — never state a "
    "percentage that isn't in the source text.\n"
    "The seller's danger is __THREAT__. Frame the entire read around THAT: does the crowd/news lean in the "
    "direction that threatens the short leg, and is there a catalyst (especially earnings) that could gap it?\n\n"
    "Return STRICT JSON ONLY (no prose before or after), with these keys:\n"
    '{"sentiment":"bullish|bearish|mixed|quiet","strength":"strong|moderate|light",'
    '"summary":"2-3 sentences on the crowd + news mood",'
    '"earnings":"the print setup — expectations / expected move / hype — or null if no near-term print",'
    '"price_levels":"levels the crowd or news is watching, or null",'
    '"trade_implication":"what this means for THIS seller\'s short leg — actionable, 1-2 sentences",'
    '"catalysts":["near-term catalysts or warnings, each one short phrase"],'
    '"confidence":"how much real signal vs noise is in this sample (high|medium|low)",'
    '"caveat":"one-line reminder this is qualitative colour, not part of the grade"}'
)


async def market_sentiment(ticker: str, trade: dict, api_key: str,
                           model: str = "gpt-4o-mini", db=None) -> dict:
    """Fetch StockTwits + news and LLM-summarize the sentiment for the user's specific trade."""
    ticker = (ticker or "").strip().upper()
    trade = trade or {}
    threat = _threat_direction(trade.get("structure"))

    social = await social_service.stocktwits_messages(db, ticker, limit=30)
    news = _fetch_news_headlines(ticker)
    st_msgs = social.get("messages") or []
    if not st_msgs and not news:
        return {"ticker": ticker,
                "error": "No recent StockTwits chatter or news is available for this ticker right now."}

    # INDEPENDENCE: pass ONLY the trade definition (to frame the read around the short leg) + public facts
    # (spot, earnings DATE) + the raw crowd/news material. NO desk outputs — no expected-move %, probability,
    # grade or fair value — so the sentiment read can't parrot our quant model (the user wants it independent).
    ctx = {
        "ticker": ticker,
        "trade": _describe_trade(trade),
        "seller_is_short": trade.get("structure"),
        "threat_direction": threat,
        "current_price": trade.get("spot"),          # public market price, for price-level context only
        "next_earnings_date": trade.get("next_earnings"),   # public calendar date (NOT an expected move)
        "days_to_expiry": trade.get("dte"),
        "stocktwits_messages": st_msgs[:30],
        "news_headlines": [{"title": n["title"], "publisher": n["publisher"], "summary": n["summary"]} for n in news],
    }
    messages = [
        {"role": "system", "content": _SYS.replace("__THREAT__", threat)},   # .replace, NOT .format — the JSON template has literal { } braces
        {"role": "user", "content": json.dumps(ctx, default=str)[:14000] + "\n\nReturn the JSON sentiment read."},
    ]
    try:
        content = await call_llm(api_key=api_key, model=model, messages=messages, max_tokens=900)
    except Exception as exc:  # noqa: BLE001
        logger.info("sentiment LLM call failed for %s: %s", ticker, exc)
        return {"ticker": ticker, "error": f"Sentiment read failed: {exc}"}

    try:
        read = json.loads(clean_json_text(content or ""))
    except Exception:
        read = {"summary": (content or "").strip()}

    return {
        "ticker": ticker,
        "read": read,
        "sources": {
            "stocktwits": {"count": len(st_msgs), "url": social.get("url")},
            "news": [{"title": n["title"], "publisher": n["publisher"], "link": n.get("link")} for n in news[:8]],
        },
        "model": model,
    }
