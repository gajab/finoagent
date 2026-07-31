"""Parse pasted broker order text (Fidelity-style label/value pairs) into legs.

Fidelity's activity/order rows paste as ``Label\\nValue`` pairs, e.g.::

    Symbol
    -SMH260918C825
    Symbol Description
    CALL (SMH) VANECK ETF TRUST SEP 18 26 $825 (100 SHS)
    Contracts
    -1
    Price
    $0.50

A multi-leg order is just several such blocks pasted back-to-back — each starts a
fresh block at its ``Symbol``. Deterministic (no LLM): the format is structured, and
users are sensitive to trade math being exactly right.
"""
from __future__ import annotations

import re
from typing import Optional

# OCC-ish option symbol: root + YYMMDD + C/P + strike. Fidelity shows the strike
# un-padded (``C825``) and prefixes a short sale with '-' (which we ignore here —
# the signed Contracts field is the source of truth for BUY/SELL).
_OCC = re.compile(r"^([A-Z]{1,6})(\d{6})([CP])(\d+(?:\.\d+)?)$")

# Labels whose NEXT line is the value. Lower-cased, ':' stripped.
_LABELS = {
    "date", "symbol", "symbol description", "description", "type", "contracts",
    "quantity", "qty", "price", "commission", "fees", "amount", "settlement date",
    "action", "acct type", "account type",
}


def _num(s: Optional[str]) -> Optional[float]:
    if s is None:
        return None
    m = re.search(r"-?\d[\d,]*(?:\.\d+)?", str(s).replace("$", ""))
    if not m:
        return None
    try:
        return float(m.group(0).replace(",", ""))
    except ValueError:
        return None


def _parse_one(o: dict) -> Optional[dict]:
    """One order block → a leg dict, or None if it isn't a recognizable trade."""
    sym = (o.get("symbol") or "").strip()
    contracts = o.get("contracts") or o.get("quantity") or o.get("qty") or ""
    qn = _num(contracts)
    price = _num(o.get("price"))
    if qn is None or qn == 0:
        return None
    action = "SELL" if qn < 0 else "BUY"
    qty = max(1, int(round(abs(qn))))

    m = _OCC.match(sym.lstrip("-+ ").upper())
    if m:
        root, ymd, right, strike = m.groups()
        exp = f"20{ymd[0:2]}-{ymd[2:4]}-{ymd[4:6]}"
        return {
            "kind": "option", "underlying": root,
            "type": "CALL" if right == "C" else "PUT", "right": right,
            "strike": float(strike), "expiration": exp,
            "action": action, "qty": qty, "price": price, "raw_symbol": sym,
        }

    # Plain stock ticker (no option encoding).
    ticker = sym.lstrip("-+ ").upper()
    if re.match(r"^[A-Z][A-Z.]{0,5}$", ticker):
        return {
            "kind": "stock", "underlying": ticker, "type": "STOCK",
            "action": action, "qty": qty, "price": price, "raw_symbol": sym,
        }
    return None


def parse_pasted_orders(text: str) -> list[dict]:
    """Pasted broker text → a list of leg dicts (one per order block)."""
    lines = [ln.strip() for ln in (text or "").splitlines() if ln.strip()]
    orders: list[dict] = []
    cur: dict = {}
    i = 0
    while i < len(lines):
        label = lines[i].lower().rstrip(":").strip()
        if label in _LABELS and i + 1 < len(lines):
            val = lines[i + 1]
            if label == "symbol" and cur.get("symbol"):
                orders.append(cur)
                cur = {}
            cur[label] = val
            i += 2
        else:
            i += 1
    if cur.get("symbol"):
        orders.append(cur)

    legs = [leg for o in orders if (leg := _parse_one(o))]
    return legs
