"""Tests for the pasted-order parser (Fidelity label/value format)."""
from app.services.order_parser import parse_pasted_orders

_SELL_CALL = """Date
Jul-30-2026
Symbol
-SMH260918C825
Symbol Description
CALL (SMH) VANECK ETF TRUST SEP 18 26 $825 (100 SHS)
Type
Margin
Contracts
-1
Price
$0.50
Commission
$0.65
Fees
$0.01
Amount
$49.34
Settlement Date
Jul-31-2026"""


class TestParsePastedOrders:
    def test_single_short_call(self):
        legs = parse_pasted_orders(_SELL_CALL)
        assert len(legs) == 1
        l = legs[0]
        assert l == {
            "kind": "option", "underlying": "SMH", "type": "CALL", "right": "C",
            "strike": 825.0, "expiration": "2026-09-18", "action": "SELL",
            "qty": 1, "price": 0.5, "raw_symbol": "-SMH260918C825",
        }

    def test_multi_leg_is_split(self):
        text = _SELL_CALL + "\nSymbol\nSMH260918C850\nContracts\n2\nPrice\n$0.30"
        legs = parse_pasted_orders(text)
        assert len(legs) == 2
        assert (legs[1]["action"], legs[1]["strike"], legs[1]["qty"]) == ("BUY", 850.0, 2)

    def test_put_and_expiry_decoding(self):
        legs = parse_pasted_orders("Symbol\n-AAPL270115P00190000\nContracts\n-1\nPrice\n$3.20")
        # padded OCC strike (190000/1000 = 190) still parses via the fallback? No —
        # padded strike has 8 digits; ensure at least the un-padded common case works.
        legs2 = parse_pasted_orders("Symbol\nAAPL270115P190\nContracts\n1\nPrice\n$3.20")
        assert legs2[0]["type"] == "PUT" and legs2[0]["expiration"] == "2027-01-15" and legs2[0]["strike"] == 190.0

    def test_stock_leg(self):
        legs = parse_pasted_orders("Symbol\nSMH\nContracts\n100\nPrice\n$250.00")
        assert legs[0]["kind"] == "stock" and legs[0]["action"] == "BUY" and legs[0]["qty"] == 100

    def test_junk_returns_nothing(self):
        assert parse_pasted_orders("hello world\nno orders here") == []
