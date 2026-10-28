"""Option CAMPAIGN math — the all-in cost basis of an open position after every partial close and roll.

A "campaign" is ONE trade row's whole option history: legs opened at entry, some closed early (partial
close), some rolled into new legs, and the legs still open today. The question a trader asks is:

    "Counting EVERY fill so far, what is my cost basis on what is open now, and at what price
     do I make money?"

Everything here is pure (no DB, no market data) and works in the app's storage convention: cash is
SELL +, BUY − (×100 per contract); a closed leg's ``realized`` is authoritative (it is what
``realized_close_pnl`` banked at close time), so ledger cash reconciles to the cent with the P&L.

Identity the whole module rests on (asserted by the tests):

    net cash so far  =  Σ opening cash of the legs STILL OPEN   +   Σ realized of every CLOSED option leg

The stock leg of a covered call is not part of the option campaign: stock realized is excluded, so
removing/closing the shares never shifts the option cost basis.
"""
from __future__ import annotations

from typing import Optional


def _is_stock(rec: dict) -> bool:
    return str((rec or {}).get("type", "")).lower() == "stock"


def _is_short(action: Optional[str]) -> bool:
    a = (action or "").lower()
    return "sell" in a or "short" in a


def _f(v, default: float = 0.0) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def split_option_realized(closed_legs: list, roll_scalar: float = 0.0, realized_scalar: Optional[float] = None) -> dict:
    """Banked OPTION realized, split by how it was banked.

    ``roll``    legs tagged ``roll`` (buy-backs inside a roll) — a cost-basis adjustment on a live campaign.
    ``partial`` every other non-stock closed leg (a genuine partial close — taking some legs off).
    ``total``   roll + partial = what the campaign has banked so far.
    ``stock``   realized on closed STOCK records (reported, never folded into the option basis).

    Legacy trades without ``closed_legs`` fall back to the stored scalars so old data still reconciles.
    """
    legs = list(closed_legs or [])
    opt = [l for l in legs if not _is_stock(l)]
    stock = round(sum(_f(l.get("realized")) for l in legs if _is_stock(l)), 2)
    if opt:
        roll = round(sum(_f(l.get("realized")) for l in opt if l.get("roll")), 2)
        partial = round(sum(_f(l.get("realized")) for l in opt if not l.get("roll")), 2)
        # Older rolls carried only the scalar — keep it when no leg is tagged.
        if roll == 0 and roll_scalar and not any(l.get("roll") for l in opt):
            roll = round(float(roll_scalar), 2)
            partial = round(partial - roll, 2)
    else:
        roll = round(float(roll_scalar or 0.0), 2)
        partial = round(float(realized_scalar or 0.0) - roll - stock, 2) if realized_scalar is not None else 0.0
    return {"roll": roll, "partial": partial, "total": round(roll + partial, 2), "stock": stock}


def _leg_desc(rec: dict) -> str:
    exp = rec.get("expiration") or rec.get("expiry")
    base = f"{str(rec.get('action') or '').upper()} {str(rec.get('type') or '').upper()} ${_f(rec.get('strike')):g}"
    return f"{base} exp {exp}" if exp else base


def _roll_descr(leg: dict) -> str:
    """Same string ``roll_position`` writes into ``rolls[].to`` — used to date a leg that was opened by a roll."""
    return f"{str(leg.get('action') or '').lower()} {str(leg.get('type') or '').lower()} ${_f(leg.get('strike')):g} exp {leg.get('expiration')}"


def _pos(l: dict) -> dict:
    """Normalise a leg/row to {side: 'short'|'long', type, strike, qty, expiration}."""
    return {"side": "short" if _is_short(l.get("action") or l.get("side")) else "long",
            "type": str(l.get("type") or "").lower(), "strike": _f(l.get("strike")),
            "qty": _f(l.get("qty"), 1.0), "expiration": l.get("expiration") or l.get("expiry")}


def describe_structure(legs: list) -> str:
    """Plain-English name of a set of option legs — what a trader would call it.

    Legs are positions ({action|side, type, strike, qty}); ``action`` 'sell' = short. Examples:
    ``short put $80`` · ``call credit spread $155/$160`` · ``iron condor — puts $70/$80 · calls $155/$160`` ·
    ``short strangle $80/$155`` · ``box spread $75/$80``.
    """
    ps = [_pos(l) for l in legs or []]
    n = len(ps)
    if n == 0:
        return "no legs"
    k = lambda v: f"${v:g}"
    if n == 1:
        p = ps[0]
        return f"{p['side']} {p['type']} {k(p['strike'])}"

    puts = sorted([p for p in ps if p["type"] == "put"], key=lambda p: p["strike"])
    calls = sorted([p for p in ps if p["type"] == "call"], key=lambda p: p["strike"])

    if n == 2 and (len(puts) == 2 or len(calls) == 2):
        lo, hi = (puts if len(puts) == 2 else calls)
        t = "put" if len(puts) == 2 else "call"
        if lo["side"] != hi["side"]:
            if lo["strike"] == hi["strike"]:
                return f"{t} calendar {k(lo['strike'])}" if lo["expiration"] != hi["expiration"] else f"{t} {k(lo['strike'])}"
            short = lo if lo["side"] == "short" else hi                   # the leg you are actually short
            long_ = hi if short is lo else lo
            credit = (short["strike"] > long_["strike"]) if t == "put" else (short["strike"] < long_["strike"])
            return f"{t} {'credit' if credit else 'debit'} spread {k(short['strike'])}/{k(long_['strike'])}"
        return f"{lo['side']} {t}s {k(lo['strike'])}/{k(hi['strike'])}"
    if n == 2 and len(puts) == 1 and len(calls) == 1:
        pu, ca = puts[0], calls[0]
        if pu["side"] == ca["side"]:
            kind = "straddle" if pu["strike"] == ca["strike"] else "strangle"
            return f"{pu['side']} {kind} {k(pu['strike'])}" if kind == "straddle" else f"{pu['side']} {kind} {k(pu['strike'])}/{k(ca['strike'])}"
        return f"{ca['side']} call {k(ca['strike'])} + {pu['side']} put {k(pu['strike'])}"
    if n == 4 and len(puts) == 2 and len(calls) == 2:
        if sorted(p["strike"] for p in puts) == sorted(c["strike"] for c in calls) and \
                puts[0]["side"] != puts[1]["side"] and calls[0]["side"] != calls[1]["side"]:
            return f"box spread {k(puts[0]['strike'])}/{k(puts[1]['strike'])}"
        sp = next((p for p in puts if p["side"] == "short"), None)
        sc = next((c for c in calls if c["side"] == "short"), None)
        lp = next((p for p in puts if p["side"] == "long"), None)
        lc = next((c for c in calls if c["side"] == "long"), None)
        if sp and sc and lp and lc:
            detail = f"puts {k(lp['strike'])}/{k(sp['strike'])} · calls {k(sc['strike'])}/{k(lc['strike'])}"
            if lp["strike"] < sp["strike"] <= sc["strike"] < lc["strike"]:
                return f"{'iron butterfly' if sp['strike'] == sc['strike'] else 'iron condor'} — {detail}"
        return f"{n}-leg options"
    return f"{n}-leg options"


def _short_date(iso: Optional[str]) -> str:
    return (iso or "")[:10]


def _expiry_label(rows: list) -> str:
    exps = sorted({str(r.get("expiration")) for r in rows if r.get("expiration")})
    return " · ".join(exps)


def _group_events(rows: list) -> list:
    """Group flat ledger rows into named EVENTS (what the trader actually did, when):

    ``open``  legs opened together at entry   ·   ``close`` legs taken off together (a partial close)
    ``roll``  legs bought back AND the replacement legs opened by the same roll.
    Rows inside an event: closes first, then opens; puts before calls; by strike.
    """
    groups: dict = {}
    order: list = []
    for r in rows:
        if r.get("roll_id"):
            key = ("roll", r["roll_id"])
        elif r["kind"] == "open":
            key = ("open", r.get("date") or "")
        else:
            key = ("close", r.get("date") or "")
        if key not in groups:
            groups[key] = []
            order.append(key)
        groups[key].append(r)

    events = []
    for key in order:
        kind = key[0]
        rs = groups[key]
        rs.sort(key=lambda r: (0 if r["kind"] == "close" else 1, 0 if r.get("type") == "put" else 1, _f(r.get("strike")), r["_seq"]))
        closes = [r for r in rs if r["kind"] == "close"]
        opens = [r for r in rs if r["kind"] == "open"]
        as_pos = lambda x, closing: {**x, "action": ("sell" if x["side"] == "buy" else "buy") if closing else x["side"]}
        if kind == "open":
            title = "Opened " + describe_structure([as_pos(r, False) for r in opens])
            subtitle = f"exp {_expiry_label(opens)}" if _expiry_label(opens) else ""
        elif kind == "close":
            title = "Closed " + describe_structure([as_pos(r, True) for r in closes])
            subtitle = f"exp {_expiry_label(closes)}" if _expiry_label(closes) else ""
        else:
            frm = describe_structure([as_pos(r, True) for r in closes]) if closes else "—"
            to = describe_structure([as_pos(r, False) for r in opens]) if opens else "nothing (closed out)"
            title = f"Rolled {frm} → {to}"
            fe, te = _expiry_label(closes), _expiry_label(opens)
            subtitle = f"{fe} → {te}" if fe and te and fe != te else (f"exp {te or fe}" if (te or fe) else "")
        date = min((r.get("date") or "") for r in rs) or None
        events.append({
            "id": f"{kind}:{key[1]}", "kind": kind, "date": date or None, "title": title, "subtitle": subtitle,
            "cash": round(sum(r["cash"] for r in rs), 2),
            "realized": round(sum(r["realized"] for r in closes), 2) if closes else None,
            "rows": rs,
        })
    order_kind = {"open": 0, "roll": 1, "close": 1}
    events.sort(key=lambda e: (str(e["date"] or ""), order_kind[e["kind"]]))
    return events


def build_campaign_ledger(open_legs: list, closed_legs: list, rolls: list, entry_date: Optional[str]) -> dict:
    """The full fill-by-fill option ledger, oldest first, with a running net cash column.

    ``open_legs``   [{action,type,strike,qty,expiration,premium}] — ``premium`` is the resolved per-share
                    entry fill (the router resolves it with the same resolver live P&L uses).
    ``closed_legs`` parameters.closed_legs (stock records are ignored here).
    ``rolls``       parameters.rolls — only used to DATE the opening of a leg that a roll opened.
    ``entry_date``  ISO date of the original entry (opening date of every non-roll-opened leg).

    Each closed leg yields TWO rows (its open and its close); each open leg yields one. Close-row cash is
    derived as ``realized − opening cash`` so the ledger sums to the banked realized exactly.
    """
    roll_dates: dict = {}          # leg description → (date, roll_id) of the roll that OPENED it
    roll_by_date: dict = {}        # roll date → roll_id (links legacy roll legs that carry no roll_id)
    for r in rolls or []:
        roll_by_date.setdefault(r.get("date"), r.get("roll_id"))
        for d in r.get("to") or []:
            roll_dates.setdefault(str(d).strip().lower(), (r.get("date"), r.get("roll_id")))

    def opened_by(leg: dict) -> tuple:
        return roll_dates.get(_roll_descr(leg).lower()) or (entry_date, None)

    rows: list[dict] = []
    seq = 0

    def add(**kw):
        nonlocal seq
        kw["_seq"] = seq
        seq += 1
        rows.append(kw)

    open_cash_open_legs = 0.0
    for l in closed_legs or []:
        if _is_stock(l):
            continue
        leg = l.get("leg") if isinstance(l.get("leg"), dict) and l.get("leg") else l
        qty = _f(l.get("qty") or leg.get("qty"), 1.0)
        short = _is_short(l.get("action") or leg.get("action"))
        entry = _f(l.get("entry_price"))
        exit_ = _f(l.get("exit_price"))
        open_cash = (1 if short else -1) * entry * qty * 100.0
        realized = _f(l.get("realized"))
        rec = {"action": l.get("action") or leg.get("action"), "type": l.get("type") or leg.get("type"),
               "strike": l.get("strike") if l.get("strike") is not None else leg.get("strike"),
               "expiration": leg.get("expiration") or leg.get("expiry"), "qty": qty}
        base = {"type": rec["type"], "strike": rec["strike"], "expiration": rec["expiration"], "qty": qty,
                "roll": bool(l.get("roll"))}                      # roll=True: this leg was ROLLED OUT
        o_date, o_rid = opened_by(leg)
        c_rid = (l.get("roll_id") or roll_by_date.get(l.get("closed_at"))) if l.get("roll") else None
        add(date=o_date, kind="open", side="sell" if short else "buy", price=round(entry, 4),
            cash=round(open_cash, 2), realized=None, roll_id=o_rid, desc=_leg_desc(rec), **base)
        add(date=l.get("closed_at"), kind="close", side="buy" if short else "sell", price=round(exit_, 4),
            entry_price=round(entry, 4), cash=round(realized - open_cash, 2), realized=round(realized, 2),
            roll_id=c_rid, desc=_leg_desc({**rec, "action": "BUY" if short else "SELL"}) + " (close)", **base)

    for l in open_legs or []:
        short = _is_short(l.get("action"))
        qty = _f(l.get("qty") or l.get("contracts"), 1.0)
        prem = _f(l.get("premium"))
        cash = (1 if short else -1) * prem * qty * 100.0
        open_cash_open_legs += cash
        o_date, o_rid = opened_by(l)
        add(date=o_date, kind="open", side="sell" if short else "buy", price=round(prem, 4), cash=round(cash, 2),
            realized=None, roll_id=o_rid, desc=_leg_desc(l), type=l.get("type"), strike=l.get("strike"),
            expiration=l.get("expiration") or l.get("expiry"), qty=qty, roll=False, still_open=True)

    # Group into named events (opened / partial close / rolled), oldest first; flatten back so the flat
    # rows and the running-cash column follow exactly the order the events tell the story in.
    events = _group_events(rows)
    flat: list[dict] = []
    running = 0.0
    for ev in events:
        for r in ev["rows"]:
            running += r["cash"]
            r["running_cash"] = round(running, 2)
            r.pop("_seq", None)
            flat.append(r)
        ev["running_cash"] = round(running, 2)
        ev["legs"] = len(ev["rows"])

    realized_total = round(sum(r["realized"] for r in flat if r["realized"] is not None), 2)
    return {
        "rows": flat,
        "events": events,
        "net_cash": round(open_cash_open_legs + realized_total, 2),
        "realized": realized_total,
        "open_cash": round(open_cash_open_legs, 2),
    }


def close_out_breakeven(effective_net: Optional[float], open_legs: list) -> Optional[dict]:
    """The price to close what is open so the WHOLE campaign finishes at ≥ $0.

    Closing every open leg at a net debit D (positive = you pay) leaves
    ``campaign_realized + open_legs_credit − D``; break-even is ``D = effective_net`` (open-leg credit +
    all banked option realized). ``per_share`` is D per share-of-the-structure (÷ 100·qty) when every open
    leg has the same quantity — i.e. the buy-back price for a single short put/call. Negative D means you
    would have to close for a net CREDIT to get back to even.
    """
    if effective_net is None or not open_legs:
        return None
    qtys = {_f(l.get("qty") or l.get("contracts"), 1.0) for l in open_legs}
    qty = qtys.pop() if len(qtys) == 1 else None
    return {
        "debit": round(effective_net, 2),
        "per_share": round(effective_net / (100.0 * qty), 4) if qty else None,
        "qty": qty,
    }
