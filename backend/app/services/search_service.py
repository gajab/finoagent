"""Search service — async proxy to SerpAPI."""

import httpx


async def web_search(api_key: str, query: str) -> list[dict]:
    """Query SerpAPI and return a list of ``{title, link, snippet}`` dicts.

    Raises ``httpx.HTTPStatusError`` on non-2xx responses from SerpAPI.
    """
    url = "https://serpapi.com/search.json"
    params = {
        "q": query,
        "api_key": api_key,
        "engine": "google",
        "num": 10,
    }

    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.get(url, params=params)
        response.raise_for_status()

    data = response.json()
    organic = data.get("organic_results", [])

    results: list[dict] = []
    for item in organic:
        results.append({
            "title": item.get("title", ""),
            "link": item.get("link", ""),
            "snippet": item.get("snippet", ""),
        })

    return results
