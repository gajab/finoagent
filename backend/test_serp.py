import asyncio
from app.services.search_service import web_search
async def test():
    try:
        await web_search("invalid_key", "test")
    except Exception as e:
        print(f"Exception type: {type(e)}")
        print(f"Exception message: {e}")
asyncio.run(test())
