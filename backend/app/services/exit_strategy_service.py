import json
import logging
from .llm_service import call_llm

logger = logging.getLogger(__name__)

async def analyze_exit_strategy(
    ticker: str,
    persona: str,
    openai_key: str,
    model: str = "gpt-4o"
) -> dict:
    """
    Evaluates a specific holding against the 6-Pillar institutional framework
    using the designated Legendary Investor Persona.
    Includes the user's compressed portfolio context to evaluate rebalancing and opportunity cost.
    """
    


    system_prompt = f"""You are a world-class institutional portfolio manager and financial strategist.
Your task is to analyze a specific stock holding ({ticker}) for a user and determine if they should 'Hold', 'Trim', or 'Sell'.
You MUST adopt the specific investment philosophy of the following Legendary Investor Persona: {persona}

Your analysis must evaluate the position against these 6 Institutional Pillars:
1. Fundamental Deterioration (Company-Specific thesis drift, management issues)
2. Macroeconomic & Monetary Policy Shifts (Systemic inflation, rate cycle changes)
3. Industry & Structural Obsolescence (Technological disruption, secular shifts)
4. Geopolitical & Regulatory Risks (Tariffs, antitrust, supply chain threats)
5. Valuation Extremes & Market Liquidity (Historical overvaluation, credit stress)
6. Market Sentiment & Flow (Insider selling, short interest spikes, institutional dumping)


Think deeply about how '{persona}' would view {ticker} given the current macro environment .

CRITICAL INSTRUCTIONS:
- Return ONLY valid JSON matching this exact schema:
{{
  "verdict": "Hold" | "Trim" | "Sell",
  "persona_summary": "A 2-3 sentence summary of what {persona} would do with this position and why.",
  "pillars": [
    {{
      "name": "Fundamental Deterioration",
      "risk_level": "Low" | "Moderate" | "High",
      "analysis": "Specific analysis of {ticker}'s fundamentals."
    }},
    {{
      "name": "Macroeconomic Shifts",
      "risk_level": "Low" | "Moderate" | "High",
      "analysis": "Specific macro analysis affecting {ticker}."
    }},
    ... (Include all 6 pillars exactly as named above) ...
  ]
}}
DO NOT include markdown code formatting like ```json in the final response. Just pure JSON.
"""

    user_prompt = f"Analyze my position in {ticker} using the {persona} persona."

    try:
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt}
        ]
        
        llm_response = await call_llm(
             api_key=openai_key,
             model=model,
             messages=messages,
             max_tokens=2500,
             expect_json=True
         )
        
        clean_json = llm_response.strip()
        if clean_json.startswith("```json"):
            clean_json = clean_json[7:]
        if clean_json.endswith("```"):
            clean_json = clean_json[:-3]
            
        parsed_llm = json.loads(clean_json.strip())

    except Exception as e:
        logger.error(f"LLM Exit Strategy Analysis failed: {e}")
        return {"error": f"Failed to analyze exit strategy with LLM: {str(e)}. Please check your OpenAI API Key."}

    return {
        "success": True,
        "analysis": parsed_llm
    }
