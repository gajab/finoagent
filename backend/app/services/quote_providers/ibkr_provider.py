"""IBKR (Interactive Brokers) quote provider via ibind.

Provides real-time quotes with Greeks — requires an active IBKR connection
with OAuth credentials configured.
"""

from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime
from functools import partial
from typing import TYPE_CHECKING

from .base import QuoteProvider, OptionQuote, UnderlyingQuote, OptionChain

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession
    from ...models import User

logger = logging.getLogger(__name__)

# ibind field IDs we request in market data snapshots
_OPTION_FIELDS = [
    "84",   # bid_price
    "86",   # ask_price
    "31",   # last_price
    "7633", # implied_vol_percent
    "7638", # option_open_interest
    "7059", # last_size (use as proxy for volume)
    "7308", # delta
    "7309", # gamma
    "7310", # theta
    "7311", # vega
    "7762", # volume_long
]

_UNDERLYING_FIELDS = [
    "84",   # bid
    "86",   # ask
    "31",   # last
    "7762", # volume
]


def _safe(v, default=0.0):
    if v is None:
        return default
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def _normalize_symbol(symbol: str) -> str:
    """Strip yfinance-style prefixes (e.g. .XSP → XSP, ^SPX → SPX) for IBKR lookups."""
    s = symbol.strip()
    if s.startswith(".") or s.startswith("^"):
        s = s[1:]
    return s.upper()


def _third_friday(year: int, month: int) -> str:
    """The standard monthly option expiry — the 3rd Friday of the month, as YYYY-MM-DD."""
    import calendar
    fridays = [d for d in calendar.Calendar().itermonthdates(year, month)
               if d.month == month and d.weekday() == 4]
    return fridays[2].strftime("%Y-%m-%d")


class IBKRProvider(QuoteProvider):
    """Real-time option quotes from Interactive Brokers Web API."""

    source_name = "ibkr"

    def __init__(self, user: "User", db: "AsyncSession") -> None:
        self._user = user
        self._db = db
        self._client = None      # lazy-loaded IbkrClient
        self._account_id = None
        self._accounts_fetched = False
        self._underlying_conid_cache = {}

    async def _ensure_client(self):
        """Build the IbkrClient on first use (blocking OAuth handshake in executor)."""
        if self._client is not None:
            return

        from ..broker.ib_service import IBService
        from ...auth import get_user_api_key

        cred_keys = [
            "ib_account_id", "ib_consumer_key", "ib_access_token",
            "ib_access_token_secret", "ib_encryption_key", "ib_signing_key",
        ]
        creds = {}
        for k in cred_keys:
            val = await get_user_api_key(self._db, self._user.id, k)
            if not val:
                raise ValueError(f"Missing IBKR credential: {k}. Configure in Settings.")
            creds[k] = val

        dh_prime = await get_user_api_key(self._db, self._user.id, "ib_dh_prime")

        def _build():
            svc = IBService(
                account_id=creds["ib_account_id"],
                consumer_key=creds["ib_consumer_key"],
                access_token=creds["ib_access_token"],
                access_token_secret=creds["ib_access_token_secret"],
                encryption_key=creds["ib_encryption_key"],
                signing_key=creds["ib_signing_key"],
                dh_prime=dh_prime,
            )
            return svc

        loop = asyncio.get_event_loop()
        svc = await loop.run_in_executor(None, _build)
        self._client = svc._client
        self._account_id = creds["ib_account_id"]

    async def _run_sync(self, func, *args, **kwargs):
        import time
        from ...database import async_session
        from ...models import ApiMetric
        loop = asyncio.get_event_loop()
        start = time.monotonic()
        status_code = 200
        try:
            result = await loop.run_in_executor(None, partial(func, *args, **kwargs))
            return result
        except Exception as e:
            status_code = getattr(e, "status_code", 500)
            raise e
        finally:
            elapsed_ms = (time.monotonic() - start) * 1000
            endpoint = getattr(func, "__name__", "unknown")
            
            async def _log_metric():
                try:
                    async with async_session() as session:
                        metric = ApiMetric(
                            provider="ibkr",
                            endpoint=endpoint,
                            method="API",
                            status_code=status_code,
                            latency_ms=elapsed_ms
                        )
                        session.add(metric)
                        await session.commit()
                except Exception as e:
                    logger.error(f"Failed to save IBKR quote metric: {e}")
                    
            asyncio.create_task(_log_metric())

    # ------------------------------------------------------------------
    # QuoteProvider interface
    # ------------------------------------------------------------------

    async def get_underlying_price(self, symbol: str) -> UnderlyingQuote:
        await self._ensure_client()

        if not self._accounts_fetched:
            await self._run_sync(self._client.receive_brokerage_accounts)
            self._accounts_fetched = True

        # Strip yfinance prefixes (.XSP → XSP, ^SPX → SPX)
        clean_symbol = _normalize_symbol(symbol)

        # Search for underlying conid
        if clean_symbol in self._underlying_conid_cache:
            conid = self._underlying_conid_cache[clean_symbol]
        else:
            result = await self._run_sync(
                self._client.search_contract_by_symbol, clean_symbol
            )
            data = result.data if hasattr(result, "data") else []
            if not data:
                raise ValueError(f"IBKR: no contract found for {clean_symbol}")

            underlying = data[0] if isinstance(data, list) else data
            conid = str(underlying.get("conid", ""))
            if not conid:
                raise ValueError(f"IBKR: no conid returned for {clean_symbol}")
            self._underlying_conid_cache[clean_symbol] = conid

        # IBKR's live snapshot INITIATES the market-data subscription on the first call and usually returns
        # EMPTY — the data lands on a follow-up call. Retry until a price shows. Log every RAW response so
        # the exact IBKR payload (or lack of it) is visible in the logs for triage.
        row: dict = {}
        for attempt in range(4):
            snap = await self._run_sync(
                self._client.live_marketdata_snapshot,
                [conid], _UNDERLYING_FIELDS,
            )
            snap_data = snap.data if hasattr(snap, "data") else snap
            logger.info("IBKR underlying snapshot %s attempt %d (conid=%s) → %r",
                        clean_symbol, attempt + 1, conid, snap_data)
            if isinstance(snap_data, list) and snap_data:
                row = snap_data[0]
            elif isinstance(snap_data, dict):
                row = snap_data
            else:
                row = {}
            if _safe(row.get("31")) > 0 or _safe(row.get("84")) > 0 or _safe(row.get("86")) > 0:
                break
            await asyncio.sleep(0.6)

        last = _safe(row.get("31"))
        bid = _safe(row.get("84"))
        ask = _safe(row.get("86"))
        vol = int(_safe(row.get("7762")))

        price = last or ((bid + ask) / 2 if bid and ask else 0)
        if price <= 0:
            # Descriptive error so the UI + logs show EXACTLY what IBKR returned (empty snapshot, error
            # code, or a payload without price fields — usually a missing market-data subscription for the
            # symbol, or delayed/closed-market data with no live fields).
            raise ValueError(
                f"IBKR returned no price for {clean_symbol} (conid={conid}) after 4 snapshots. "
                f"Requested fields {_UNDERLYING_FIELDS}; last payload keys={list(row.keys())}, row={row!r}. "
                f"Common cause: no live market-data subscription for {clean_symbol} on this IBKR account, "
                f"or the market is closed (live snapshot has no fields)."
            )

        return UnderlyingQuote(
            symbol=clean_symbol,
            price=price,
            source="ibkr",
            bid=bid or None,
            ask=ask or None,
            volume=vol or None,
        )

    async def get_option_expirations(self, symbol: str) -> list[str]:
        await self._ensure_client()

        if not self._accounts_fetched:
            await self._run_sync(self._client.receive_brokerage_accounts)
            self._accounts_fetched = True

        clean_symbol = _normalize_symbol(symbol)
        
        if clean_symbol in self._underlying_conid_cache:
            under_conid = self._underlying_conid_cache[clean_symbol]
            # Since get_option_expirations relies on the sections array from the search result,
            # we must perform the search anyway if we only cached the conid string.
            # But we can optimize this if needed, for now we let it fall through 
            # to search if we don't have the full object.
        
        result = await self._run_sync(
            self._client.search_contract_by_symbol, clean_symbol
        )
        data = result.data if hasattr(result, "data") else []
        if not data:
            return []

        underlying = data[0] if isinstance(data, list) else data
        
        # Cache the conid while we're here
        if "conid" in underlying and clean_symbol not in self._underlying_conid_cache:
            self._underlying_conid_cache[clean_symbol] = str(underlying["conid"])
        # ibind returns available months in the search results
        sections = underlying.get("sections", [])
        months = set()
        for section in sections:
            for m in section.get("months", "").split(";"):
                m = m.strip()
                if m:
                    months.add(m)

        # IBKR's search returns available MONTHS (MMMYY), not exact dates. Standard monthly options expire
        # the 3rd Friday — resolve to that real date so DTE is correct and the expiry matches what the
        # frontend (and _select_expirations' exact-date match) expects. (Weeklies aren't in the month list.)
        expiration_dates = []
        for m in sorted(months):
            dt = None
            for fmt in ("%b%y", "%Y%m"):
                try:
                    dt = datetime.strptime(m, fmt); break
                except Exception:
                    continue
            if dt is not None:
                expiration_dates.append(_third_friday(dt.year, dt.month))

        return sorted(expiration_dates)

    async def get_single_option_quote(
        self, symbol: str, expiration: str, strike: float, right: str
    ) -> OptionQuote | None:
        """Fast path: fetch bid/ask/mid for ONE specific option contract.

        Only 3 IBKR API calls (search underlying → secdef for 1 strike → snapshot),
        instead of the full chain fetch which does dozens.
        """
        await self._ensure_client()
        if not self._accounts_fetched:
            await self._run_sync(self._client.receive_brokerage_accounts)
            self._accounts_fetched = True

        clean_symbol = _normalize_symbol(symbol)

        # 1. Resolve underlying conid
        if clean_symbol in self._underlying_conid_cache:
            under_conid = self._underlying_conid_cache[clean_symbol]
        else:
            result = await self._run_sync(
                self._client.search_contract_by_symbol, clean_symbol
            )
            data = result.data if hasattr(result, "data") else []
            if not data:
                return None

            underlying = data[0] if isinstance(data, list) else data
            under_conid = str(underlying.get("conid", ""))
            if not under_conid:
                return None
            self._underlying_conid_cache[clean_symbol] = under_conid

        month_str = _expiry_to_ib_month(expiration)

        # 2. Secdef lookup for this one strike+right
        try:
            secdef_result = await self._run_sync(
                self._client.search_secdef_info_by_conid,
                under_conid, "OPT", month_str,
                strike=str(strike), right=right.upper(),
            )
        except Exception as exc:
            logger.warning(f"Secdef lookup failed for {clean_symbol} {right} {strike}: {exc}")
            return None

        secdef_data = secdef_result.data if hasattr(secdef_result, "data") else []
        if isinstance(secdef_data, dict):
            secdef_data = [secdef_data]
        if not isinstance(secdef_data, list) or not secdef_data:
            return None

        # Pick the item closest to the requested strike
        best = min(secdef_data, key=lambda x: abs(float(x.get("strike", 0)) - strike))
        conid = str(best.get("conid", ""))
        if not conid:
            return None

        # 3. Snapshot for this single conid — retry up to 2 times
        snap_data: list = []
        for attempt in range(3):
            snap = await self._run_sync(
                self._client.live_marketdata_snapshot,
                [conid], _OPTION_FIELDS,
            )
            snap_data = snap.data if hasattr(snap, "data") else []
            if not isinstance(snap_data, list):
                snap_data = [snap_data] if isinstance(snap_data, dict) else []
                _r0 = snap_data[0] if snap_data else {}
            if snap_data and (_safe(_r0.get("84")) > 0 or _safe(_r0.get("86")) > 0 or _safe(_r0.get("31")) > 0.01):
                break
            if attempt < 2:
                await asyncio.sleep(1.5 * (attempt + 1))
        if not snap_data:
            return None

        row = snap_data[0]
        bid = _safe(row.get("84"))
        ask = _safe(row.get("86"))
        last = _safe(row.get("31"))
        mid = round((bid + ask) / 2, 4) if bid > 0 and ask > 0 else last

        return OptionQuote(
            strike=float(best.get("strike", strike)),
            right=right.upper(),
            expiration=best.get("maturityDate", expiration),
            bid=bid,
            ask=ask,
            last=last,
            mid=mid,
            iv=_safe(row.get("7633")),
            oi=int(_safe(row.get("7638"))),
            volume=int(_safe(row.get("7762", row.get("7059", 0)))),
            delta=_safe(row.get("7308")) or None,
            gamma=_safe(row.get("7309")) or None,
            theta=_safe(row.get("7310")) or None,
            vega=_safe(row.get("7311")) or None,
            conid=int(conid) if conid.isdigit() else None,
        )

    async def get_multiple_option_quotes(
        self, requests: list[dict]
    ) -> list[OptionQuote | None]:
        """Fast path: fetch bid/ask/mid for MULTIPLE option contracts in one batch.

        Expects list of dicts: {"symbol", "expiration", "strike", "right"}
        Uses 1 underlying search, N secdef lookups, and 1 batch snapshot.
        """
        await self._ensure_client()
        if not self._accounts_fetched:
            await self._run_sync(self._client.receive_brokerage_accounts)
            self._accounts_fetched = True

        if not requests:
            return []

        clean_symbol = _normalize_symbol(requests[0]["symbol"])

        # 1. Resolve underlying conid (using cache if available)
        if clean_symbol in self._underlying_conid_cache:
            under_conid = self._underlying_conid_cache[clean_symbol]
        else:
            result = await self._run_sync(
                self._client.search_contract_by_symbol, clean_symbol
            )
            data = result.data if hasattr(result, "data") else []
            if not data:
                return [None] * len(requests)

            underlying = data[0] if isinstance(data, list) else data
            under_conid = str(underlying.get("conid", ""))
            if not under_conid:
                return [None] * len(requests)
            self._underlying_conid_cache[clean_symbol] = under_conid

        # 2. Collect conids for all requested legs via secdef info
        conid_to_idx = {}  # conid -> leg index mapping
        conids_to_fetch = []
        for i, req in enumerate(requests):
            month_str = _expiry_to_ib_month(req["expiration"])
            try:
                secdef_result = await self._run_sync(
                    self._client.search_secdef_info_by_conid,
                    under_conid, "OPT", month_str,
                    strike=str(req["strike"]), right=req["right"].upper(),
                )
            except Exception as exc:
                logger.warning(f"Secdef lookup failed for req {req}: {exc}")
                continue

            secdef_data = secdef_result.data if hasattr(secdef_result, "data") else []
            if isinstance(secdef_data, dict):
                secdef_data = [secdef_data]
            if not isinstance(secdef_data, list) or not secdef_data:
                continue

            best = min(secdef_data, key=lambda x: abs(float(x.get("strike", 0)) - req["strike"]))
            conid = str(best.get("conid", ""))
            if conid:
                conids_to_fetch.append(conid)
                conid_to_idx[conid] = i

        if not conids_to_fetch:
            return [None] * len(requests)

        # 3. Snapshot for all conids in one batch — retry up to 2 times
        #    IBKR may return partial/empty data on first call while session warms up.
        snap_data: list = []
        for attempt in range(3):
            snap = await self._run_sync(
                self._client.live_marketdata_snapshot,
                conids_to_fetch, _OPTION_FIELDS,
            )
            snap_data = snap.data if hasattr(snap, "data") else []
            if not isinstance(snap_data, list):
                snap_data = [snap_data] if isinstance(snap_data, dict) else []

            # Check if ALL requested conids have bid or last price populated
            returned_conids = set()
            for row in snap_data:
                row_conid = str(row.get("conid", row.get("6008", "")))
                if row_conid and (row.get("84") or row.get("31")):
                    returned_conids.add(row_conid)

            missing = set(conids_to_fetch) - returned_conids
            if not missing:
                break  # all legs populated
            logger.info(
                f"IBKR snapshot attempt {attempt + 1}: {len(returned_conids)}/{len(conids_to_fetch)} legs populated, "
                f"{len(missing)} missing — {'retrying' if attempt < 2 else 'proceeding with partial data'}"
            )
            if attempt < 2:
                await asyncio.sleep(1.5 * (attempt + 1))  # 1.5s, 3s

        # Map back to results
        results = [None] * len(requests)
        for row in snap_data:
            row_conid = str(row.get("conid", row.get("6008", "")))
            if row_conid not in conid_to_idx:
                continue

            idx = conid_to_idx[row_conid]
            req = requests[idx]
            
            bid = _safe(row.get("84"))
            ask = _safe(row.get("86"))
            last = _safe(row.get("31"))
            mid = round((bid + ask) / 2, 4) if bid > 0 and ask > 0 else last

            # Reject rows where IBKR returned 0 for all price fields — the session
            # subscribed but hasn't received market data yet. Treat as missing so
            # the retry loop picks it up.
            if bid == 0 and ask == 0 and last < 0.01:
                continue

            results[idx] = OptionQuote(
                strike=req["strike"],
                right=req["right"].upper(),
                expiration=req["expiration"],
                bid=bid,
                ask=ask,
                last=last,
                mid=mid,
                iv=_safe(row.get("7633")) or None,
                oi=int(_safe(row.get("7638"))),
                volume=int(_safe(row.get("7762", row.get("7059", 0)))),
                delta=_safe(row.get("7308")) or None,
                gamma=_safe(row.get("7309")) or None,
                theta=_safe(row.get("7310")) or None,
                vega=_safe(row.get("7311")) or None,
                conid=int(row_conid) if row_conid.isdigit() else None,
            )

        return results

    async def get_option_chain(
        self, symbol: str, expiration: str
    ) -> OptionChain:
        await self._ensure_client()

        # Pre-flight
        if not self._accounts_fetched:
            await self._run_sync(self._client.receive_brokerage_accounts)
            self._accounts_fetched = True

        # Strip yfinance prefixes
        clean_symbol = _normalize_symbol(symbol)

        # Search underlying
        if clean_symbol in self._underlying_conid_cache:
            under_conid = self._underlying_conid_cache[clean_symbol]
        else:
            result = await self._run_sync(
                self._client.search_contract_by_symbol, clean_symbol
            )
            data = result.data if hasattr(result, "data") else []
            if not data:
                raise ValueError(f"IBKR: no contract found for {clean_symbol}")

            underlying = data[0] if isinstance(data, list) else data
            under_conid = str(underlying.get("conid", ""))
            if not under_conid:
                raise ValueError(f"IBKR: no conid returned for {clean_symbol}")
            self._underlying_conid_cache[clean_symbol] = under_conid

        # Convert expiration YYYY-MM-DD → MMMYY
        month_str = _expiry_to_ib_month(expiration)

        # Get strikes
        strikes_result = await self._run_sync(
            self._client.search_strikes_by_conid,
            under_conid, "OPT", month_str,
        )
        strikes_data = strikes_result.data if hasattr(strikes_result, "data") else {}
        call_strikes = strikes_data.get("call", [])
        put_strikes = strikes_data.get("put", [])
        all_strikes = sorted(set(call_strikes + put_strikes))

        if not all_strikes:
            return OptionChain(
                symbol=clean_symbol,
                expiration=expiration,
                underlying_price=0,
                quotes=[],
                source="ibkr",
            )

        # Get underlying price
        try:
            uq = await self.get_underlying_price(symbol)
            underlying_price = uq.price
        except Exception:
            underlying_price = 0

        # Filter strikes to ±15% of underlying (or all if price unknown)
        # to avoid hundreds of slow secdef lookups
        if underlying_price > 0:
            low_bound = underlying_price * 0.85
            high_bound = underlying_price * 1.15
            call_strikes = [s for s in call_strikes if low_bound <= s <= high_bound]
            put_strikes = [s for s in put_strikes if low_bound <= s <= high_bound]
            logger.info(
                f"IBKR: filtered to {len(call_strikes)}C + {len(put_strikes)}P strikes "
                f"within ±15% of {underlying_price:.0f} for {clean_symbol}"
            )

        # Get secdef info for each strike+right combo (IBKR requires strike)
        quotes: list[OptionQuote] = []
        conid_to_meta: dict[str, dict] = {}

        for right in ["C", "P"]:
            right_strikes = call_strikes if right == "C" else put_strikes
            for strike_val in right_strikes:
                try:
                    secdef_result = await self._run_sync(
                        self._client.search_secdef_info_by_conid,
                        under_conid, "OPT", month_str,
                        strike=str(strike_val), right=right,
                    )
                    secdef_data = secdef_result.data if hasattr(secdef_result, "data") else []

                    if isinstance(secdef_data, list):
                        for item in secdef_data:
                            conid = str(item.get("conid", ""))
                            strike = float(item.get("strike", 0))
                            if conid and strike:
                                conid_to_meta[conid] = {
                                    "strike": strike,
                                    "right": right,
                                    "expiration": item.get("maturityDate", expiration),
                                }
                    elif isinstance(secdef_data, dict):
                        conid = str(secdef_data.get("conid", ""))
                        strike = float(secdef_data.get("strike", 0))
                        if conid and strike:
                            conid_to_meta[conid] = {
                                "strike": strike,
                                "right": right,
                                "expiration": secdef_data.get("maturityDate", expiration),
                            }
                except Exception as exc:
                    logger.debug(f"Secdef lookup failed for {clean_symbol} {right} {strike_val}: {exc}")
                    continue

        if not conid_to_meta:
            return OptionChain(
                symbol=clean_symbol,
                expiration=expiration,
                underlying_price=underlying_price,
                quotes=[],
                source="ibkr",
            )

        # Batch snapshot — ibind supports multiple conids
        conids = list(conid_to_meta.keys())
        # Process in batches of 50 to avoid timeout
        batch_size = 50
        n_batches = (len(conids) + batch_size - 1) // batch_size
        for i in range(0, len(conids), batch_size):
            batch = conids[i:i + batch_size]
            try:
                # IBKR computes IV + greeks (7633, 7308-7311) ASYNCHRONOUSLY: the first snapshot(s)
                # routinely come back EMPTY, then price-only, with IV landing a few polls later. The old
                # code only retried when a NON-empty first response lacked price — so an empty first call
                # (the common warm-up case) was never retried, and IV (which lags price) was never waited
                # for → every quote had iv=0 → the smile couldn't fit → the whole vol panel went blank.
                # Poll until at least one contract reports IV, logging every attempt's shape so the raw
                # IBKR behaviour (empty / priced-but-no-IV / full) is visible for triage.
                snap_data: list = []
                n_iv = n_px = 0
                for attempt in range(5):
                    snap = await self._run_sync(
                        self._client.live_marketdata_snapshot,
                        batch, _OPTION_FIELDS,
                    )
                    raw = snap.data if hasattr(snap, "data") else snap
                    snap_data = raw if isinstance(raw, list) else ([raw] if isinstance(raw, dict) else [])
                    rows = [r for r in snap_data if isinstance(r, dict)]
                    n_iv = sum(1 for r in rows if _safe(r.get("7633")) > 0)
                    n_px = sum(1 for r in rows if _safe(r.get("31")) > 0 or _safe(r.get("84")) > 0)
                    logger.info(
                        "IBKR option batch %s [%d/%d] attempt %d → %d rows, %d priced, %d with IV(7633); sample=%r",
                        clean_symbol, i // batch_size + 1, n_batches, attempt + 1,
                        len(rows), n_px, n_iv, (rows[0] if rows else None),
                    )
                    if n_iv > 0:
                        break
                    await asyncio.sleep(0.8)

                for row in snap_data:
                    if not isinstance(row, dict):
                        continue
                    conid = str(row.get("conid", row.get("6008", "")))
                    meta = conid_to_meta.get(conid)
                    if not meta:
                        continue

                    bid = _safe(row.get("84"))
                    ask = _safe(row.get("86"))
                    last = _safe(row.get("31"))
                    mid = round((bid + ask) / 2, 4) if bid > 0 and ask > 0 else last

                    iv = _safe(row.get("7633"))
                    oi = int(_safe(row.get("7638")))
                    vol = int(_safe(row.get("7762", row.get("7059", 0))))
                    delta = _safe(row.get("7308")) or None
                    gamma = _safe(row.get("7309")) or None
                    theta = _safe(row.get("7310")) or None
                    vega = _safe(row.get("7311")) or None

                    quotes.append(OptionQuote(
                        strike=meta["strike"],
                        right=meta["right"],
                        expiration=meta["expiration"],
                        bid=bid,
                        ask=ask,
                        last=last,
                        mid=mid,
                        iv=iv / 100 if iv > 1 else iv,  # normalize to decimal
                        oi=oi,
                        volume=vol,
                        delta=delta,
                        gamma=gamma,
                        theta=theta,
                        vega=vega,
                        conid=int(conid) if conid.isdigit() else None,
                    ))
            except Exception as exc:
                logger.warning(f"IBKR snapshot batch failed: {exc}")
                continue

        return OptionChain(
            symbol=clean_symbol,
            expiration=expiration,
            underlying_price=underlying_price,
            quotes=quotes,
            source="ibkr",
        )


def _expiry_to_ib_month(expiry: str) -> str:
    """Convert YYYY-MM-DD or YYYYMMDD to IB month format MMMYY."""
    clean = expiry.replace("-", "")
    if len(clean) >= 8:
        d = datetime.strptime(clean[:8], "%Y%m%d")
    else:
        d = datetime.strptime(clean[:6], "%Y%m")
    return d.strftime("%b%y").upper()  # e.g. "APR26"
