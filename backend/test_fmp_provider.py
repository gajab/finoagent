import asyncio
import os
import sys

# Ensure backend folder is in path so we can import app.providers
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.providers.factory import get_market_data_provider

async def test_fmp():
    print("Testing FMP Provider Implementation...")
    # Requires FMP_API_KEY set in the environment
    
    # If keys are missing, we expect a ValueError
    try:
        provider = get_market_data_provider("fmp")
    except ValueError as e:
        print(f"Skipping test - {e}")
        return

    print("Fetching quote for AAPL...")
    try:
        quote = await provider.get_quote("AAPL")
        print(f"Quote Result: {quote.model_dump_json(indent=2)}")
    except Exception as e:
        print(f"Failed to fetch quote: {e}")

    print("\nFetching company info for AAPL...")
    try:
        info = await provider.get_company_info("AAPL")
        print(f"Info Result: {info.model_dump_json(indent=2)}")
    except Exception as e:
        print(f"Failed to fetch info: {e}")

    print("\nFetching recent 1Day bars for AAPL...")
    try:
        bars = await provider.get_historical_bars("AAPL", timeframe="1Day", limit=3)
        print(f"Bars Result ({len(bars)} bars found):")
        for bar in bars:
            print(f"  {bar.model_dump_json()}")
    except Exception as e:
        print(f"Failed to fetch bars: {e}")

if __name__ == "__main__":
    asyncio.run(test_fmp())
