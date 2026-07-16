"""Interactive Brokers Web API integration via ibind library.

Connects directly to https://api.ibkr.com/v1/api/ using OAuth 1.0a.
No local gateway required.
"""

import asyncio
import logging
from functools import partial

from ibind import IbkrClient, OrderRequest as IbOrderRequest
from ibind.oauth.oauth1a import OAuth1aConfig

from ...database import async_session
from ...models import ApiMetric

from .base import (
    BrokerService,
    BrokerAuthStatus,
    ContractInfo,
    OrderRequest,
    OrderResult,
)

logger = logging.getLogger(__name__)


def _build_oauth_config(
    consumer_key: str,
    access_token: str,
    access_token_secret: str,
    encryption_key: str,
    signing_key: str,
    dh_prime: str | None = None,
) -> OAuth1aConfig:
    """Build ibind OAuth1aConfig from user-provided credentials."""
    config = OAuth1aConfig()
    config.consumer_key = consumer_key
    config.access_token = access_token
    config.access_token_secret = access_token_secret
    config.encryption_key = encryption_key
    config.signature_key = signing_key
    if dh_prime:
        config.dh_prime = dh_prime
    config.init_oauth = True
    config.maintain_oauth = False   # Don't spawn a tickler thread — clients are short-lived
    config.init_brokerage_session = True
    return config


def _create_ib_client(
    account_id: str,
    consumer_key: str,
    access_token: str,
    access_token_secret: str,
    encryption_key: str,
    signing_key: str,
    dh_prime: str | None = None,
) -> IbkrClient:
    """Create an IbkrClient configured for direct Web API access via OAuth."""
    oauth_config = _build_oauth_config(
        consumer_key=consumer_key,
        access_token=access_token,
        access_token_secret=access_token_secret,
        encryption_key=encryption_key,
        signing_key=signing_key,
        dh_prime=dh_prime,
    )
    return IbkrClient(
        account_id=account_id,
        use_oauth=True,
        oauth_config=oauth_config,
        timeout=15,
        max_retries=2,
    )


class IBService(BrokerService):
    """Interactive Brokers broker service using ibind + direct Web API."""

    def __init__(
        self,
        account_id: str,
        consumer_key: str,
        access_token: str,
        access_token_secret: str,
        encryption_key: str,
        signing_key: str,
        dh_prime: str | None = None,
    ):
        self._account_id = account_id
        self._client = _create_ib_client(
            account_id=account_id,
            consumer_key=consumer_key,
            access_token=access_token,
            access_token_secret=access_token_secret,
            encryption_key=encryption_key,
            signing_key=signing_key,
            dh_prime=dh_prime,
        )

    def shutdown(self):
        """Stop any background threads (tickler) started by ibind."""
        try:
            self._client.stop_tickler()
        except Exception:
            pass

    async def _run_sync(self, func, *args, **kwargs):
        """Run a synchronous ibind call in an executor to avoid blocking the event loop."""
        import time
        loop = asyncio.get_event_loop()
        start = time.monotonic()
        status_code = 200
        try:
            result = await loop.run_in_executor(None, partial(func, *args, **kwargs))
            return result
        except Exception as e:
            status_code = 500
            raise e
        finally:
            elapsed_ms = (time.monotonic() - start) * 1000
            endpoint = getattr(func, '__name__', 'unknown')
            asyncio.create_task(self._log_metric(endpoint, elapsed_ms, status_code))

    async def _log_metric(self, endpoint: str, latency: float, status: int):
        try:
            async with async_session() as session:
                metric = ApiMetric(
                    provider="ibkr",
                    endpoint=endpoint,
                    method="API",
                    status_code=status,
                    latency_ms=latency
                )
                session.add(metric)
                await session.commit()
        except Exception as e:
            logger.error(f"Failed to save IB metric: {e}")

    async def check_connection(self) -> BrokerAuthStatus:
        try:
            result = await self._run_sync(self._client.tickle)
            data = result.data if hasattr(result, "data") else {}

            auth_result = await self._run_sync(self._client.authentication_status)
            auth_data = auth_result.data if hasattr(auth_result, "data") else {}

            authenticated = auth_data.get("authenticated", False)
            server_name = data.get("ssoExpires") or data.get("serverName")

            return BrokerAuthStatus(
                connected=True,
                authenticated=authenticated,
                account_id=self._account_id,
                server_name=str(server_name) if server_name else None,
            )
        except Exception as e:
            logger.warning(f"IB connection check failed: {e}")
            return BrokerAuthStatus(
                connected=False,
                authenticated=False,
                error=str(e),
            )

    async def get_accounts(self) -> list[dict]:
        try:
            result = await self._run_sync(self._client.receive_brokerage_accounts)
            data = result.data if hasattr(result, "data") else result
            if isinstance(data, dict) and "accounts" in data:
                return data["accounts"]
            if isinstance(data, list):
                return data
            return [{"id": self._account_id}]
        except Exception as e:
            logger.error(f"Failed to get IB accounts: {e}")
            return []

    async def search_contract(
        self,
        symbol: str,
        sec_type: str = "OPT",
        expiry: str | None = None,
        strike: float | None = None,
        right: str | None = None,
    ) -> list[ContractInfo]:
        try:
            # Normalize symbol: strip yfinance-style prefixes (^XSP → XSP, .SPX → SPX)
            clean_symbol = symbol.strip()
            if clean_symbol.startswith("^") or clean_symbol.startswith("."):
                clean_symbol = clean_symbol[1:]
            clean_symbol = clean_symbol.upper()

            # Step 1: Search for the underlying
            search_result = await self._run_sync(
                self._client.search_contract_by_symbol, clean_symbol
            )
            search_data = search_result.data if hasattr(search_result, "data") else []

            if not search_data:
                return []

            # Get the first match's conid (underlying)
            underlying = search_data[0] if isinstance(search_data, list) else search_data
            under_conid = str(underlying.get("conid", ""))

            if sec_type == "STK":
                return [ContractInfo(
                    conid=int(under_conid),
                    symbol=clean_symbol,
                    sec_type="STK",
                    exchange=underlying.get("exchange", "SMART"),
                    description=underlying.get("companyName"),
                )]

            if not expiry:
                return []

            # Step 2: Convert expiry format (YYYY-MM-DD → MMMYY)
            month_str = _expiry_to_ib_month(expiry)

            # Step 3: Get available strikes
            strikes_result = await self._run_sync(
                self._client.search_strikes_by_conid,
                under_conid, sec_type, month_str
            )

            # Step 4: Get specific contract info
            secdef_result = await self._run_sync(
                self._client.search_secdef_info_by_conid,
                under_conid,
                sec_type,
                month_str,
                strike=str(strike) if strike else None,
                right=right,
            )
            secdef_data = secdef_result.data if hasattr(secdef_result, "data") else []

            contracts = []
            if isinstance(secdef_data, list):
                for item in secdef_data:
                    contracts.append(ContractInfo(
                        conid=int(item.get("conid", 0)),
                        symbol=clean_symbol,
                        sec_type=sec_type,
                        exchange=item.get("exchange", "SMART"),
                        expiry=item.get("maturityDate"),
                        strike=float(item.get("strike", 0)),
                        right=item.get("right"),
                        description=item.get("symbol"),
                    ))

            return contracts

        except Exception as e:
            logger.error(f"IB contract search failed for {symbol}: {e}")
            return []

    async def place_order(
        self,
        account_id: str,
        order: OrderRequest,
    ) -> OrderResult:
        # Round price to valid tick to avoid IBKR rejection
        rounded_price = self._round_to_tick(order.price) if order.price else order.price
        try:
            ib_order = IbOrderRequest(
                conid=order.conid,
                side=order.side,
                quantity=order.quantity,
                order_type=order.order_type,
                acct_id=account_id,
                price=rounded_price,
                tif=order.tif,
            )

            result = await self._run_sync(
                self._client.place_order,
                ib_order,
                {},  # answers dict for IB confirmation prompts (empty = auto-accept)
                account_id,
            )

            data = result.data if hasattr(result, "data") else result

            # IB may return order_id directly or in a nested structure
            if isinstance(data, list) and data:
                first = data[0]
                order_id = str(first.get("order_id", first.get("orderId", "")))
                status = first.get("order_status", first.get("orderStatus", "submitted"))
                return OrderResult(
                    success=True,
                    order_id=order_id,
                    message=f"Order {status}",
                )
            elif isinstance(data, dict):
                order_id = str(data.get("order_id", data.get("orderId", "")))
                return OrderResult(
                    success=True,
                    order_id=order_id,
                    message=data.get("order_status", "submitted"),
                )
            else:
                return OrderResult(
                    success=True,
                    message="Order submitted",
                )

        except Exception as e:
            logger.error(f"IB order placement failed: {e}")
            return OrderResult(
                success=False,
                error=str(e),
            )

    async def place_multi_order(
        self,
        account_id: str,
        orders: list[OrderRequest],
    ) -> list[OrderResult]:
        """Place multiple legs as a single multi-leg order submission.

        IBKR Web API accepts an array of orders in one POST — legs submitted
        together are understood as a strategy/combo rather than separate singles.
        This prevents naked-leg margin rejections.
        """
        ib_orders = []
        for order in orders:
            rounded_price = self._round_to_tick(order.price) if order.price else order.price
            ib_orders.append(IbOrderRequest(
                conid=order.conid,
                side=order.side,
                quantity=order.quantity,
                order_type=order.order_type,
                acct_id=account_id,
                price=rounded_price,
                tif=order.tif,
            ))

        try:
            result = await self._run_sync(
                self._client.place_order,
                ib_orders,
                {},  # auto-accept IBKR confirmation prompts
                account_id,
            )

            data = result.data if hasattr(result, "data") else result

            # Parse response — IBKR may return per-order results or a single batch result
            results: list[OrderResult] = []

            if isinstance(data, list):
                for item in data:
                    if isinstance(item, dict):
                        order_id = str(item.get("order_id", item.get("orderId", "")))
                        status = item.get("order_status", item.get("orderStatus", "submitted"))
                        results.append(OrderResult(
                            success=True,
                            order_id=order_id if order_id else None,
                            message=f"Order {status}",
                        ))
                # If we got fewer results than orders, fill remaining as success
                while len(results) < len(orders):
                    results.append(OrderResult(success=True, message="Order submitted"))
            elif isinstance(data, dict):
                order_id = str(data.get("order_id", data.get("orderId", "")))
                # Single result for the batch — apply to all
                for _ in orders:
                    results.append(OrderResult(
                        success=True,
                        order_id=order_id if order_id else None,
                        message="Order submitted (batch)",
                    ))
            else:
                for _ in orders:
                    results.append(OrderResult(success=True, message="Order submitted"))

            return results

        except Exception as e:
            logger.error(f"IB multi-leg order placement failed: {e}")
            return [OrderResult(success=False, error=str(e)) for _ in orders]

    @staticmethod
    def _round_to_tick(price: float, tick_size: float | None = None) -> float:
        """Round price to valid IBKR tick increment.

        Index options (SPX/XSP/NDX) use 0.05 ticks for prices < $3,
        0.10 ticks for prices >= $3. Auto-detects when tick_size is None.
        """
        if price <= 0:
            return 0.0
        if tick_size is None:
            tick_size = 0.05 if price < 3.0 else 0.10
        return round(round(price / tick_size) * tick_size, 2)

    async def preview_order(
        self,
        account_id: str,
        conid: int,
        side: str,
        quantity: int,
        price: float,
    ) -> dict:
        """Preview / whatif order without submitting.

        Returns estimated commission, margin impact, and any warnings.
        """
        # Round to valid tick size to avoid IBKR "minimum price variation" rejection
        price = self._round_to_tick(price)

        try:
            ib_order = IbOrderRequest(
                conid=conid,
                side=side,
                quantity=quantity,
                order_type="LMT",
                acct_id=account_id,
                price=price,
                tif="DAY",
            )

            # ibind exposes whatif_order if available; fall back to order_reply
            result = None
            if hasattr(self._client, "whatif_order"):
                result = await self._run_sync(
                    self._client.whatif_order, ib_order, account_id
                )
            elif hasattr(self._client, "place_order_whatif"):
                result = await self._run_sync(
                    self._client.place_order_whatif, ib_order, account_id
                )

            if result and hasattr(result, "data") and result.data:
                data = result.data
                return {
                    "commission": data.get("commission"),
                    "margin_impact": data.get("marginChange") or data.get("margin"),
                    "equity_with_loan": data.get("equityWithLoanBefore"),
                    "warnings": [data["warn"]] if isinstance(data.get("warn"), str) else (data.get("warn") or []),
                }

            # Fallback: return basic info if whatif not available
            return {
                "commission": None,
                "margin_impact": None,
                "warnings": ["Order preview (whatif) not supported — estimate unavailable. Order will be placed directly."],
            }

        except Exception as e:
            logger.warning(f"Order preview failed: {e}")
            return {
                "commission": None,
                "margin_impact": None,
                "warnings": [f"Preview unavailable: {e}"],
            }

    async def get_order_status(self, order_id: str) -> dict:
        try:
            result = await self._run_sync(self._client.order_status, order_id)
            return result.data if hasattr(result, "data") else {}
        except Exception as e:
            logger.error(f"Failed to get order status {order_id}: {e}")
            return {"error": str(e)}


def _expiry_to_ib_month(expiry: str) -> str:
    """Convert YYYY-MM-DD or YYYYMMDD to IB month format MMMYY."""
    import datetime as dt
    clean = expiry.replace("-", "")
    d = dt.datetime.strptime(clean[:8], "%Y%m%d") if len(clean) >= 8 else dt.datetime.strptime(clean[:6], "%Y%m")
    return d.strftime("%b%y").upper()  # e.g. "APR26"
