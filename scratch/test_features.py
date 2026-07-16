import asyncio
import json
from app.database import async_session
from app.auth import get_user_api_key, decrypt_value
from app.routers.stock_router import _generate_ai_analysis, get_rupee_metrics, format_rupee_context
from app.services.llm_service import call_llm
from sqlalchemy import select
from app.models import User

async def run_tests():
    async with async_session() as db:
        user = (await db.execute(select(User).where(User.email == "karwa.rahul@gmail.com"))).scalar_one_or_none()
        if not user:
            print("User not found!")
            return

        print(f"=== TESTING USER: {user.email} ===")

        # ----------------------------------------------------
        # Part 1: Test RUPEE Framework Analysis
        # ----------------------------------------------------
        ticker = "AAPL"
        metrics = await get_rupee_metrics(ticker)
        company = metrics.get("name", ticker)
        extra_context = format_rupee_context(metrics)

        rupee_prompt = f"""Act as a shrewd, traditional Indian business owner (a Marwari/Baniya "Sethji") who evaluates publicly traded companies strictly as if buying the entire physical business ("dukaan") with your own hard-earned money.

Ignore Wall Street fluff like "Adjusted EBITDA" or "TAM projections." You only care about capital protection, cold hard cash, low debt, and buying at a massive bargain (the classic Dhandho framework: "Heads I win, tails I don't lose much").

Evaluate the company: {company}.

Open the Bahi-Khata (ledger) and analyze this business using the RUPEE Framework. For EACH letter provide "The Reality" (hard financial numbers/metrics from the ledger) and "The Marwari Verdict" (your interpretation using traditional business sense and relevant Hindi/Marwari jargon).

The five sections, in order:
- R — Rokda (Cash Generation): Focus on Free Cash Flow and FCF margin. Is the galla (cash box) filling up, or is the business bleeding cash?
- U — Udhaari (Debt & Leverage): Focus on Total Debt-to-Equity and interest coverage. Are we slaves to the bank paying byaj (interest), or is the balance sheet clean?
- P — Price (Valuation): Focus on P/E, Price-to-Book, and Margin of Safety. Are we buying at kabaad ke bhav (scrap value), or paying the euphoria premium?
- E — Excellence (Moat, Quality & Margins): Focus on ROE/ROIC, Net & Gross Margins, and Economic Moat. Is there an ek-chhatra raj (monopoly/moat), or is it low-margin mehnat ki roti that barely beats a fixed deposit?
- E — Earnings (Growth & Trajectory): Focus on EPS Growth (trailing AND forward) and Revenue Growth. Is the dhandha actually growing its bottom line, or shrinking? Be skeptical of forward EPS jumps that come from a depressed GAAP base vs adjusted analyst estimates.

Then give the final ruling — "Dhandho Kharido Ya Nahi?" — choosing exactly ONE stance:
- "Saaf Inkaar" (Absolutely Not / Bleeding Cash / High Debt)
- "Taareef Karo, Par Kharido Mat" (Great business, but price too high / Wait for a drop)
- "Ghar le aao" (Great Business, Great Price / Buy it now)

Keep the tone witty, pragmatic, ruthlessly focused on cash, and highly skeptical of management promises.

YOU MUST RETURN ONLY A VALID JSON OBJECT WITH THIS EXACT STRUCTURE:
{{
  "company": "{company}",
  "sections": [
    {{"key": "R", "title": "Rokda (Cash Generation)", "reality": "hard numbers...", "verdict": "the Marwari verdict with jargon..."}},
    {{"key": "U", "title": "Udhaari (Debt & Leverage)", "reality": "...", "verdict": "..."}},
    {{"key": "P", "title": "Price (Valuation)", "reality": "...", "verdict": "..."}},
    {{"key": "E1", "title": "Excellence (Moat, Quality & Margins)", "reality": "...", "verdict": "..."}},
    {{"key": "E2", "title": "Earnings (Growth & Trajectory)", "reality": "...", "verdict": "..."}}
  ],
  "stance": "Ghar le aao",
  "verdict": "final definitive ruling explaining why, in the Sethji voice..."
}}"""

        # Helper to update model in DB so get_user_api_key resolves the correct key/endpoint
        from sqlalchemy import delete
        from app.models import UserApiKey
        from app.auth import encrypt_value
        from app.services.llm_service import active_model

        async def set_model(model_name):
            # Delete existing preference
            await db.execute(
                delete(UserApiKey).where(
                    UserApiKey.user_id == user.id,
                    UserApiKey.key_name == "openai_model"
                )
            )
            # Add new preference
            db.add(UserApiKey(
                user_id=user.id,
                key_name="openai_model",
                encrypted_value=encrypt_value(model_name)
            ))
            await db.commit()
            # Clear ContextVar
            active_model.set(None)

        # Store original model preference to restore at the end
        orig_model_res = await db.execute(
            select(UserApiKey).where(
                UserApiKey.user_id == user.id,
                UserApiKey.key_name == "openai_model"
            )
        )
        orig_row = orig_model_res.scalar_one_or_none()
        orig_model = decrypt_value(orig_row.encrypted_value) if orig_row else None

        # Test A: Gemini RUPEE
        print("\n--- 1A. RUPEE Analysis with Gemini Model (gemini-2.5-flash) ---")
        await set_model("gemini-2.5-flash")
        await asyncio.sleep(5)  # Wait for rate limit window
        
        try:
            gemini_key = await get_user_api_key(db, user.id, "gemini_api_key")
            raw_res = await call_llm(api_key=gemini_key, model="gemini-2.5-flash", messages=[
                {"role": "system", "content": rupee_prompt + f"\n\nHere is the current data for this company:\n{extra_context}"},
                {"role": "user", "content": "Generate the rupee analysis for AAPL. Return ONLY valid JSON."}
            ], max_tokens=2500, expect_json=False)
            print("Raw Gemini RUPEE response:")
            print(repr(raw_res[:200]) + "..." + repr(raw_res[-200:]))
            
            res_gemini = await _generate_ai_analysis(ticker, "rupee", rupee_prompt, user, db, extra_context=extra_context)
            print("Gemini RUPEE Stance:", res_gemini["analysis"]["stance"])
            print("Gemini RUPEE Verdict excerpt:", res_gemini["analysis"]["verdict"][:120], "...")
            print("SUCCESS: Gemini RUPEE response correctly parsed as JSON.")
        except Exception as e:
            print("FAILED Gemini RUPEE:", e)

        # Test B: OpenAI RUPEE
        print("\n--- 1B. RUPEE Analysis with OpenAI Model (gpt-4o-mini) ---")
        await set_model("gpt-4o-mini")
        await asyncio.sleep(5)  # Wait for rate limit window
        
        try:
            # We fetch the raw response before calling _generate_ai_analysis to debug
            openai_key = await get_user_api_key(db, user.id, "openai_api_key")
            raw_res = await call_llm(api_key=openai_key, model="gpt-4o-mini", messages=[
                {"role": "system", "content": rupee_prompt + f"\n\nHere is the current data for this company:\n{extra_context}"},
                {"role": "user", "content": "Generate the rupee analysis for AAPL. Return ONLY valid JSON."}
            ], max_tokens=2500, expect_json=False)
            print("Raw OpenAI RUPEE response:")
            print(repr(raw_res[:200]) + "..." + repr(raw_res[-200:]))
            
            res_openai = await _generate_ai_analysis(ticker, "rupee", rupee_prompt, user, db, extra_context=extra_context)
            print("OpenAI RUPEE Stance:", res_openai["analysis"]["stance"])
            print("OpenAI RUPEE Verdict excerpt:", res_openai["analysis"]["verdict"][:120], "...")
            print("SUCCESS: OpenAI RUPEE response correctly parsed as JSON.")
        except Exception as e:
            print("FAILED OpenAI RUPEE:", e)


        # ----------------------------------------------------
        # Part 2: Test AI News Summary
        # ----------------------------------------------------
        news_system_prompt = f"""You are a financial analyst summarizing recent news for ticker {ticker}.
Create a brief, high-impact news digest.
Analyze the positive, negative, and neutral sentiment count, identify the top 3 headlines, and extract the primary recurring theme.

YOU MUST RETURN ONLY A VALID JSON OBJECT WITH THE FOLLOWING EXACT STRUCTURE:
{{
  "totalArticles": 5,
  "sentiment": {{
    "positive": 2,
    "negative": 1,
    "neutral": 2
  }},
  "topHeadlines": [
    "Headline 1",
    "Headline 2",
    "Headline 3"
  ],
  "theme": "Macroeconomic Policy / Product Launch / Earnings",
  "digest": "A 2-3 sentence executive summary of the overall news tone and major highlights."
}}
Return ONLY valid JSON, no markdown fences, no explanation."""

        news_user_content = """Summarize and analyze these 3 recent news articles:
1. Apple launches new iPad Pro with M4 chip, driving stock to all-time high. (Positive)
2. EU antitrust regulator fines Apple $1.8 billion over App Store music streaming restrictions. (Negative)
3. Apple's upcoming WWDC conference expected to showcase new AI features. (Neutral)"""

        messages = [
            {"role": "system", "content": news_system_prompt},
            {"role": "user", "content": news_user_content}
        ]

        # Test C: Gemini News Summary
        print("\n--- 2A. News Summary with Gemini Model (gemini-2.5-flash) ---")
        await set_model("gemini-2.5-flash")
        await asyncio.sleep(5)  # Wait for rate limit window
        gemini_key = await get_user_api_key(db, user.id, "gemini_api_key")
        try:
            raw_summary = await call_llm(
                api_key=gemini_key,
                model="gemini-2.5-flash",
                messages=messages,
                max_tokens=1200,
                expect_json=False
            )
            print("Raw Gemini News response:")
            print(repr(raw_summary[:200]) + "..." + repr(raw_summary[-200:]))
            
            cleaned_summary = await call_llm(
                api_key=gemini_key,
                model="gemini-2.5-flash",
                messages=messages,
                max_tokens=1200,
                expect_json=True
            )
            parsed_summary = json.loads(cleaned_summary)
            print("Gemini News Theme:", parsed_summary.get("theme"))
            print("Gemini News Digest:", parsed_summary.get("digest"))
            print("SUCCESS: Gemini News Summary parsed perfectly.")
        except Exception as e:
            print("FAILED Gemini News Summary:", e)

        # Test D: OpenAI News Summary
        print("\n--- 2B. News Summary with OpenAI Model (gpt-4o-mini) ---")
        await set_model("gpt-4o-mini")
        await asyncio.sleep(5)  # Wait for rate limit window
        openai_key = await get_user_api_key(db, user.id, "openai_api_key")
        try:
            raw_summary = await call_llm(
                api_key=openai_key,
                model="gpt-4o-mini",
                messages=messages,
                max_tokens=1200,
                expect_json=False
            )
            print("Raw OpenAI News response:")
            print(repr(raw_summary[:200]) + "..." + repr(raw_summary[-200:]))

            cleaned_summary = await call_llm(
                api_key=openai_key,
                model="gpt-4o-mini",
                messages=messages,
                max_tokens=1200,
                expect_json=True
            )
            parsed_summary = json.loads(cleaned_summary)
            print("OpenAI News Theme:", parsed_summary.get("theme"))
            print("OpenAI News Digest:", parsed_summary.get("digest"))
            print("SUCCESS: OpenAI News Summary parsed perfectly.")
        except Exception as e:
            print("FAILED OpenAI News Summary:", e)

        # Restore original model preference
        if orig_model:
            await set_model(orig_model)
        else:
            await db.execute(
                delete(UserApiKey).where(
                    UserApiKey.user_id == user.id,
                    UserApiKey.key_name == "openai_model"
                )
            )
            await db.commit()

if __name__ == "__main__":
    asyncio.run(run_tests())
