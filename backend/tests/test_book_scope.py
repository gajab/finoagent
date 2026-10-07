"""Manage Book scope: only trades tagged Income feed the book-level tail-risk desk."""
import json
from types import SimpleNamespace

from app.routers.saved_strategy_router import (
    _book_purpose, _income_book, _book_scope, _no_income_msg, _book_tr_key,
)


def _t(purpose="__absent__", raw=None, tid=1):
    if raw is not None:
        params = raw
    elif purpose == "__absent__":
        params = json.dumps({})
    else:
        params = json.dumps({"purpose": purpose})
    return SimpleNamespace(id=tid, parameters=params)


class TestPurpose:
    def test_a_trade_with_no_tag_is_untagged_not_income(self):
        # Manage Book does not guess: only an explicit Income tag counts
        assert _book_purpose(_t()) == "untagged"
        assert _book_purpose(SimpleNamespace(id=1, parameters=None)) == "untagged"
        assert _book_purpose(_t("")) == "untagged"

    def test_unknown_or_garbled_purpose_is_untagged(self):
        assert _book_purpose(_t("speculation")) == "untagged"
        assert _book_purpose(_t(raw="{not json")) == "untagged"
        assert _book_purpose(_t(raw=json.dumps(["a", "list"]))) == "untagged"

    def test_known_purposes_are_case_insensitive(self):
        assert _book_purpose(_t("INCOME")) == "income"
        assert _book_purpose(_t("HEDGE")) == "hedge"
        assert _book_purpose(_t(" Trade ")) == "trade"
        assert _book_purpose(_t("managed_floor")) == "managed_floor"


class TestIncomeBook:
    def test_only_explicitly_tagged_income_trades_are_kept_and_the_rest_are_counted_by_folder(self):
        book = [_t("income", tid=1), _t(tid=2), _t("hedge", tid=3), _t("trade", tid=4), _t("hedge", tid=5), _t("other", tid=6), _t("income", tid=7)]
        income, excluded = _income_book(book)
        assert [s.id for s in income] == [1, 7]                 # the untagged trade (2) and 'other' (6) are OUT
        assert excluded == {"untagged": 1, "hedge": 2, "trade": 1, "other": 1}

    def test_nothing_excluded_when_everything_is_income(self):
        income, excluded = _income_book([_t("income"), _t("income")])
        assert len(income) == 2 and excluded == {}

    def test_untagged_only_book_is_empty(self):
        income, excluded = _income_book([_t(), _t(tid=2)])
        assert income == [] and excluded == {"untagged": 2}

    def test_scope_summary_for_the_ui(self):
        income, excluded = _income_book([_t("income"), _t("hedge"), _t("trade"), _t(tid=4)])
        assert _book_scope(income, excluded) == {"purpose": "income", "included": 1, "excluded": {"hedge": 1, "trade": 1, "untagged": 1}, "excluded_total": 3}

    def test_empty_income_book_explains_what_was_ignored(self):
        _, excluded = _income_book([_t("hedge"), _t("trade")])
        msg = _no_income_msg(excluded)
        assert "tagged Income" in msg and "2 active trades" in msg
        assert _no_income_msg({}) == "No active trades tagged Income to analyze."


def test_stored_snapshots_from_earlier_scopes_are_not_reused():
    # v1 covered every active trade, v2 also counted untagged trades, v3 predates covered-share netting and live-quote fixes
    assert _book_tr_key(7) == "book_tail_risk:v4:7"


# ── the endpoints really hand ONLY Income trades to the engine ─────────────────
import asyncio
import pytest


class _Res:
    def __init__(self, rows): self._rows = rows
    def scalars(self): return self
    def all(self): return self._rows


class _Db:
    def __init__(self, rows): self.rows = rows
    async def execute(self, *_a, **_k): return _Res(self.rows)


@pytest.fixture
def wired(monkeypatch):
    """Patch the engine + cache; return the list the engine was called with."""
    import app.services.book_tail_risk as eng
    import app.services.cache_service as cs
    seen, stored = {}, {}

    async def fake_compute(strategies, quote_source, user, db, **_k):
        seen["ids"] = [s.id for s in strategies]
        return {"positions": len(strategies), "cvar_95": -1000.0}

    async def fake_get(db, key): return stored.get(key)
    async def fake_set(db, key, val, ttl_seconds=None): stored[key] = val

    monkeypatch.setattr(eng, "compute_book_tail_risk", fake_compute)
    monkeypatch.setattr(cs, "get_cached", fake_get)
    monkeypatch.setattr(cs, "set_cached", fake_set)
    return seen, stored


def test_book_tail_risk_endpoint_analyzes_only_income_and_reports_the_scope(wired):
    from app.routers.saved_strategy_router import book_tail_risk
    seen, stored = wired
    book = [_t("income", tid=1), _t(tid=2), _t("hedge", tid=3), _t("trade", tid=4), _t("income", tid=5)]
    out = asyncio.run(book_tail_risk(quote_source="yfinance", refresh=True, user=SimpleNamespace(id=9), db=_Db(book)))
    assert seen["ids"] == [1, 5]                                     # the untagged trade (2) is not analysed
    assert out["scope"] == {"purpose": "income", "included": 2, "excluded": {"untagged": 1, "hedge": 1, "trade": 1}, "excluded_total": 3}
    assert stored[_book_tr_key(9)]["scope"]["included"] == 2        # the saved snapshot carries the scope too


def test_book_tail_risk_endpoint_with_no_income_trades_says_so_and_stores_nothing(wired):
    from app.routers.saved_strategy_router import book_tail_risk
    seen, stored = wired
    out = asyncio.run(book_tail_risk(quote_source="yfinance", refresh=True, user=SimpleNamespace(id=9), db=_Db([_t("hedge", tid=3)])))
    assert "ids" not in seen and not stored
    assert "Income" in out["error"] and "1 active trade " in out["error"] + " "
