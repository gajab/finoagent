import asyncio
from app.services.dual_direction_service import run_dual_direction_buffer

async def main():
    res = await run_dual_direction_buffer("SPY", 68700.0, 365, 15.0, 15.0)
    print("Cost:", res.get("actualStructureCost"))
    print("Net Premium:", res.get("netOptionsPremium"))
    print("\nLegs:")
    for leg in res.get("legs", []):
         print(leg)
    print("\nScenarios:")
    for s in res.get("scenarios", []):
         print(s)

if __name__ == "__main__":
    asyncio.run(main())
