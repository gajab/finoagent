# Breadth / participation, market-cap concentration, and factor crowding as predictors of short-horizon (1–20 day) US index outcomes

Evidence-strength key used throughout: **STRONG** = peer-reviewed, long sample, replicated or out-of-sample. **MODERATE** = peer-reviewed but single study, or no clean out-of-sample test, or the horizon differs from 1–20 days. **WEAK** = practitioner backtests, short or small samples. **FOLKLORE** = rules of thumb with no independent test found.

Monitor context (from the codebase, not from web sources): `backend/app/services/market_internals_service.py` computes breadth from **today's** S&P 500 list (`datasets/s-and-p-500-companies` CSV) over ~440 calendar days. It z-scores ETF factor spreads over a 3-session window against σ from the 60 sessions *before* the window. It labels "Crowded-leadership unwind" when SPY is down, any of momentum/beta/semis/growth spreads is ≤ −1.5σ, and RSP−SPY is > 0. Its docstring already says that 14 months of today's constituents "can't give an unbiased history (survivorship)".

---

## Q1. Academic vs practitioner evidence on breadth (A/D, new highs/lows, % above MA, TRIN/TICK, Zweig thrust, Lowry 90% days). Do narrow markets predict worse forward returns?

### Takeaway
The peer-reviewed evidence that breadth predicts index returns is thin. The most rigorous result points toward **continuation**: Zaremba et al. (2021) find high-breadth market portfolios outperform low-breadth ones over monthly horizons across 64 countries. It does not support "breadth divergence ⇒ crash", and older work (Brown & Cliff) finds breadth-type sentiment measures move *with* the market but barely predict it over weeks to a month. The specific triggers the monitor uses have **no independent peer-reviewed out-of-sample test**, and their published "track records" are tiny-sample NYSE statistics from practitioners. Those triggers are the Zweig thrust, Lowry 90% days, % above 20/50/200-DMA washout levels, new-high/new-low counts and TRIN/TICK.

### Cited Findings
**Academic (peer-reviewed)**
- **Zaremba, Szyszka, Karathanasopoulos & Mikutowski (2021), *Economic Modelling* 97:348–364, "Herding for profits"** (MODERATE). They define breadth as the average number of rising minus falling stocks in a portfolio. Over **64 countries, 1973–2018**, it is a robust predictor of future returns on **market and industry portfolios**: high-breadth portfolios significantly outperform low-breadth ones. This holds after controlling for size, style, volatility, skewness, momentum and **trend-following signals**. The effect is stronger where limits to arbitrage are high, after bullish periods, and in collectivistic societies, which the authors read as herding. They warn that frequent rebalancing raises transaction costs — [IDEAS/RePEc abstract](https://ideas.repec.org/a/eee/ecmode/v97y2021icp348-364.html); [SSRN 3444882](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=3444882). *The monthly spread magnitude was not retrievable (paywall); see Gaps.*
- The same authors write that its predictive power is not just momentum, the 52-week-high effect or moving averages repackaged. They describe earlier US breadth studies as reaching "mixed conclusions" — [ResearchGate summary](https://www.researchgate.net/publication/340699814_Herding_for_profits_Market_breadth_and_the_cross-section_of_global_equity_returns).
- **Brown & Cliff (2004), *Journal of Empirical Finance*, "Investor sentiment and the near-term stock market"** (MODERATE, and against short-horizon prediction). Direct survey sentiment and *indirect* technical measures, including **advance/decline** and put/call, are related to each other and to contemporaneous returns. There is **little evidence they predict short-term (weekly/monthly) market returns** — [CXO Advisory summary](https://www.cxoadvisory.com/sentiment-indicators/does-investor-sentiment-give-trading-signals).
- **Neely, Rapach, Tu & Zhou (2014), *Management Science* 60:1772–1791** (STRONG for the general claim; these are not breadth indicators). Index-level technical indicators show statistically and economically significant in-sample **and out-of-sample** monthly equity-premium predictability that matches or beats macro variables, and combining the two helps further. The indicators are MA crossovers, 3–12-month momentum and on-balance volume — [WUSTL profile](https://profiles.wustl.edu/en/publications/forecasting-the-equity-risk-premium-the-role-of-technical-indicat/). These are *index* trend signals, not cross-sectional participation counts.
- **Li & Yu (2012), *JFE*, "Investor attention, psychological anchors, and stock return predictability"** (MODERATE; replicated in Taiwan and Pakistan). **Nearness of the Dow to its 52-week high predicts *higher* future aggregate returns**, while nearness to its *historical* high predicts *lower* returns — [abstract](https://academicnewsletter.sufe.edu.cn/info/356820). This uses the *index's* 52-week high, not counts of constituents making new highs or lows.
- **Yu, Webb & Lin (2025), *Journal of Investing* 34(2)** (WEAK; practitioner-academic journal). They test index-specific market breadth, **TRIN**, and % of stocks above their **20/50/200-day MA**, including whether breadth computed on a broader index predicts better. The conclusion is only that "some technical strategies may be effective" — [Montclair record](https://researchwith.montclair.edu/en/publications/the-use-of-index-specific-market-breadth-and-index-over-moving-av/).
- Origin of A/D data: Col. Leonard Ayres, 1926 — [Wikipedia](https://en.wikipedia.org/wiki/Advance-Decline_Data).

**Practitioner (flagged)**
- **Zweig Breadth Thrust** (WEAK/FOLKLORE as a forecaster)
  - The rule: the 10-day EMA of adv/(adv+dec) moves from <0.40 to >0.615 within 10 sessions.
  - Claimed record on Zweig's NYSE data: about **20 signals since 1945, averaging ~+23% over the next year**, with no 6- or 12-month losers since 1950. These figures cannot be independently recomputed.
  - On a broader common-stock universe, **6 thrusts since 2010** were all followed by SPY being higher 12 months later, averaging **+14.9%**. The source itself flags high uncertainty from the tiny sample.
  - **Universe sensitivity:** thrusts on **2023-11-03 and 2025-04-24 fired on NYSE data but peaked at 0.612 on the broader universe**, just under the 0.615 bar — [thetrading.tools](https://www.thetrading.tools/concepts/zweig-breadth-thrust).
  - A SentimenTrader persistence variant (breadth >58% for 8 sessions, double signals) has only **5 occurrences** (1962, 1975, 1982, 2018, 2025) — [SentimenTrader](https://sentimentrader.com/blog/a-variation-of-the-zweig-breadth-thrust-indicator-triggered-an-alert).
- **Lowry 90% days** (WEAK/FOLKLORE)
  - Source: Paul Desmond's 2002 Dow Award paper "Identifying Bear Market Bottoms and New Bull Markets", built on **NYSE** daily up/down volume *and* points.
  - Bottoms feature *clusters* of 90% downside days; the 1973–74 bear had **15** of them over about 15–16 months.
  - The signal is a **90% upside day after the selling series**. A single panic day is not a signal — [Ritholtz Q&A with Desmond](https://ritholtz.com/2011/02/qa-paul-desmond-of-lowrys-part-ii).
  - Third-party implementations also accept two back-to-back 80% up days, and caution that vendor volume differs from WSJ/NYSE figures — [TradingView script](https://kr.tradingview.com/script/bKz4L0Mw-90-Days).
  - In 2002 Desmond rejected a strong July rally because its big up day was <85% — [EliteTrader repost of MarketWatch](https://www.elitetrader.com/et/threads/lowry-90-down-day-article-update.7351/post-96221).
- **% above 200-DMA extremes** (WEAK)
  - Morgan Stanley's Mike Wilson (Jan 2021) noted that 92% of S&P 500 stocks above their 200-DMA (98th percentile over 30 years) was followed by **lower 5-day, 1-month and 3-month forward returns**.
  - Schaeffer's found that ~95% readings preceded weak 6-month (+1.9% average) but strong 12-month returns — [Schaeffer's 2021](https://www.schaeffersresearch.com/content/analysis/2021/04/21/can-the-s-p-500-index-keep-up-its-impressive-run).
  - Re-crossing 50% after a long stretch below gave an average **−0.07% next week, +0.3% next month, higher only about half the time** — [Schaeffer's 2019](https://www.schaeffersresearch.com/content/analysis/2019/02/13/stocks-verging-on-signal-not-seen-in-3-years).
  - No rigorous washout-threshold study was found. "Low tens" is described as practitioner convention, and the levels shift across markets and decades — [LuxAlgo](https://www.luxalgo.com/library/concept/percent-stocks-above-20-50-200-day-ma/).
- **"Narrow breadth ⇒ drawdown"** (WEAK; Goldman Sachs sell-side, inconsistent over time)
  - **2020:** "Sharp declines in market breadth in the past have often signaled large market drawdowns… Narrow breadth can last for extended periods, but past episodes have signaled below-average market returns and eventual momentum reversals" — [ai-CIO](https://www.ai-cio.com/news/bad-breadth-5-giants-market-dominance-grim-sign-goldman-says/).
  - **2018:** the same team said narrow depth was *not* worrying because earnings were broad — [Bloomberg](https://www.bloomberg.com/news/articles/2018-07-30/dearth-of-depth-in-u-s-stock-rally-isn-t-scaring-goldman-sachs).
  - **2021:** "The macro environment does not suggest drawdown risk is elevated" despite narrowing breadth — [Fin24](https://m.wheels24.co.za/fin24/economy/goldman-sachs-sees-risk-of-major-stocks-draw-down-as-modest-for-now-20211211).

### Inferences
- The only peer-reviewed breadth predictor found (Zaremba et al.) is a **positive/continuation** relation at the monthly horizon: weak breadth predicts weaker returns, strong breadth stronger. That runs opposite to the contrarian "washout ⇒ bounce" reading behind Zweig and Lowry. The two can coexist: the contrarian signals are *extreme-event* rules on a different horizon. But no academic study validates the extreme-event thresholds.
- Brown & Cliff plus the continuation result imply that breadth is mostly **descriptive of the current move** (how broad, how forced) and only weakly predictive at 1–20 days. The monitor should present breadth as diagnosis, not forecast.
- Zweig (0.40/0.615) and Lowry (90%) thresholds were calibrated on **NYSE issue/volume counts** of roughly 3,000 issues including preferreds and closed-end funds. On an S&P 500-only universe they are a *different indicator*. The 0.612-vs-0.615 near-misses show that a handful of names flips the signal.
- Signal counts are tiny: about 20 Zweig thrusts in 80 years, and a few Lowry clusters per bear market. A 12-month "always up" record over n≈6–20 has a wide confidence interval. A Wilson 95% interval on 6/6 is roughly 61%–100%, against an unconditional 12-month up-rate for the S&P of about 70–75% (an approximate base rate from general knowledge, not from a source in these notes).

### Gaps
- Zaremba et al.'s high-minus-low breadth return spread (%/month), its t-stats, and any US-only time-series result: full text was paywalled (SSRN 403).
- No peer-reviewed out-of-sample test of the Zweig thrust, Lowry 90% days, NYSE TICK, Hindenburg Omen or "breadth divergence" was found. TICK and Hindenburg Omen: no academic evidence located at all. Treat them as FOLKLORE.
- The "Kwon, Lowry & Qiu" concentration/breadth paper named in the brief could not be located. It may not exist under that author set.
- Brown & Cliff's (2005, *Journal of Business*) long-horizon (multi-year) sentiment predictability is referenced in the literature but was not verified this session.

---

## Q2. Survivorship bias of computing historical breadth from today's constituents: how large, and how to avoid it?

### Takeaway
The bias is **directional and systematic**, not noise. Today's list over-represents stocks that *rose into* the index and omits stocks that *fell out*. The past therefore looks better than it was: higher % above MA, fewer new lows, and "median stock vs index" biased up. The literature on S&P additions documents large pre-inclusion run-ups, so the bias is material even over a 1-year lookback. The fix is point-in-time membership.

### Cited Findings
- Using only current members to build history "would add survivorship bias". Free sources such as Yahoo and broker APIs usually lack delisted price histories. The fix is a point-in-time membership table that records each stock's in-index "spells" — [QuantConnect forum](https://www.quantconnect.com/forum/discussion/21226/would-you-pay-for-cheap-point-in-time-s-amp-p-500-data-survivorship-bias-free/); [Concretum/Norgate workflow](https://concretumgroup.com/the-complete-norgate-data-toolkit-three-python-workflows-every-quant-researcher-needs/).
- **Magnitude proxy, index additions:** S&P 500-added firms show "extraordinary pre-event inclusion price momentum" linked to strong earnings growth — [NY Fed Staff Report 484](https://www.newyorkfed.org/medialibrary/media/research/staff_reports/sr484.html). A study summarised by Morningstar finds added firms earned about **77% abnormal returns in the 3 years before joining**, after which the pattern reversed — [Morningstar](https://global.morningstar.com/en-gb/funds/sp-500-bump-that-doesnt-last).
- A forum post cites a 1.5–2%/yr bias in backtest returns, but that figure is **unsourced** — [QuantConnect forum](https://www.quantconnect.com/forum/discussion/21226/would-you-pay-for-cheap-point-in-time-s-amp-p-500-data-survivorship-bias-free/). Treat it as unverified.
- The monitor's own docstring acknowledges the issue and declines to compute historical percentiles for breadth for this reason (`backend/app/services/market_internals_service.py`, lines 20–21).

### Inferences
- **Direction of the bias, per metric, when looking back from today's list:**
  - % above 20/50/200-DMA: biased **up** in the past. Additions were rising; deletions, which were falling, are missing.
  - 52-week new lows: biased **down** in the past.
  - New highs: biased **up**.
  - "% of members down over the window": biased **down**.
  - Median stock vs cap-weighted index: biased toward the median looking **better** historically.
  - Every *current* reading is unbiased, because current membership is correct today. The bias only affects **historical comparisons, percentiles, and anything with a lookback**: the 52-week high/low window, the 200-DMA, the 10-day Zweig EMA history, and z-scores against past breadth.
- **Size over the monitor's ~14-month window:** small but non-zero, and concentrated in the tails. In any year a few dozen names change. Deletions are typically names that have collapsed (exactly the stocks that would print new lows), and additions are typically names that have run up. In a market-wide washout the missing deletions under-count new lows and % below 200-DMA the most, which is precisely when the monitor is read.
- **Implementable fixes, cheapest first:**
  1. Keep computing *today's* breadth from today's list (unbiased now). Never show "percentile vs history" for breadth computed this way. The monitor already follows this.
  2. For history, reconstruct point-in-time membership from a public changes list. Candidates, not verified this session: the "Selected changes" table on Wikipedia's S&P 500 list, or the community repo `fja05680/sp500`. Keep deleted names' prices where yfinance still has them, and **mark history incomplete** when a deleted ticker has no data, rather than silently dropping it.
  3. Or use an exchange-wide breadth series that is survivorship-free by construction (NYSE/Nasdaq advance-decline, new highs/lows and up/down volume from a data vendor). This is also the universe on which the Zweig and Lowry thresholds were calibrated.
  4. Report the **count of names with full history** next to every breadth stat so the denominator is visible.

### Gaps
- No peer-reviewed estimate was found of the bias *specifically for breadth statistics* (as opposed to backtest returns) from current-constituent lists. It would have to be measured in-house by comparing current-list vs point-in-time breadth over 2000–2025.
- Annual S&P 500 turnover counts were not verified this session.

---

## Q3. Factor crowding and crowded-leadership unwinds (momentum crashes): evidence, crowding measures, unwind dynamics, and what follows for the INDEX vs the FACTOR

### Takeaway
This is the **strongest-evidence** part of the brief, and it carries a key twist for the monitor. Classic **momentum crashes** (Daniel & Moskowitz 2016) are forecastable from an *ex-ante panic state*: a negative 24-month market return plus high 126-day market variance. But the crash happens on the **rebound**, when the index rallies hard and past losers "crash up". They are not index selloffs.

Crowding *de-leveraging* shocks are a different animal. In the 2007 quant quake (Khandani & Lo) the losses were largely market-neutral, concentrated in 2–3 days, and **partly reversed within 1 day**. Lou & Polk's comomentum robustly predicts weaker momentum returns and more "bad" momentum days over the following months. Holdings-based crowding is a weaker and contested tail-risk predictor (Barroso, Edelen & Karehnke). Leader-led selloffs in a down tape with equal-weight outperforming (July 2024, DeepSeek Jan 2025) are documented only as case studies, with no systematic forward-return evidence for the index.

### Cited Findings
**Daniel & Moskowitz (2016, *JFE*), "Momentum crashes"** — STRONG (US 1927–2013, plus international equities and other asset classes). All figures below are from [NBER WP 20439 full text](https://www.nber.org/system/files/working_papers/w20439/w20439.pdf); [abstract](https://www.nber.org/papers/w20439).
- **Sample:** US, 1927:01–2013:03.
  - WML (winners-minus-losers): Sharpe **0.71** vs the market's 0.40; beta −0.58; CAPM alpha **22.3%/yr** (t=8.5).
  - WML skewness: **−4.70 monthly, −1.18 daily**.
- **Crashes are short-leg melt-ups during market rebounds:**
  - July–Aug 1932: the market rose **+82%**; losers **+232%**, winners +32%.
  - Mar–May 2009: the market rose **+26%**; losers **+163%**, winners +8%. In early March 2009 the loser decile was on average down **84%** from peak (Citi, BofA, Ford, GM).
- **The worst WML months coincide with big *up* market months after deep 2-year declines.** For example:
  - 1932-08: WML −74.4%, market +36.5% that month, prior 2-year market −67.8%.
  - 2009-04: WML −45.5%, market +10.2%, prior 2-year market −40.6%.
- **Ex-ante state variables:**
  - Bear indicator I_B = 1 if the **past-24-month cumulative CRSP VW return < 0**.
  - Market variance = **126-day** daily variance.
- **Effect sizes:**
  - In bear markets, WML beta is **1.13 lower** (t=−13.4) and alpha falls to about **−6.4 bp/month**.
  - Option-like asymmetry: up-market beta **−1.51** vs down-market −0.70 (t of difference 4.5).
  - WML mean is **31.5%/yr** outside bear states. In "panic" states (bear × high variance, variance-weighted) the mean is **~59%/yr lower**.
- **Dynamic, forecast-scaled momentum approximately doubles alpha and Sharpe.** Across markets and asset classes it reaches Sharpe **1.18**.
- **Crashes can be persistent:**
  - From 1 June 1932 to 30 Dec 1939, losers beat winners by about 50%.
  - From 8 March 2009 to 28 March 2013, losers more than doubled winners' return.

**Barroso & Santa-Clara (2015, *JFE* 116:111–120), "Momentum has its moments"** — STRONG, widely replicated. Momentum risk is time-varying and **predictable** from its own recent realised variance. Scaling exposure by that forecast "virtually eliminates crashes and nearly doubles the Sharpe ratio" — [UCP record](https://ciencia.ucp.pt/en/publications/momentum-has-its-moments/). *Exact kurtosis and Sharpe figures were not verified this session.*

**Lou & Polk (2022, *RFS* 35(7):3272–3302), "Comomentum"** — STRONG for the factor (US 1965–2015; AQR Insight Award honourable mention). All figures from [LSE PDF](https://personal.lse.ac.uk/polk/research/hhab117.pdf); [LSE eprint](https://eprints.lse.ac.uk/109318/).
- **Measure:** the average pairwise *abnormal* correlation, from **52 weekly (Fri–Fri) returns** in the formation year, among loser-decile and winner-decile stocks.
- **Mechanism:** low comomentum means momentum corrects underreaction. High comomentum means momentum stocks "strongly revert" from crowded overreaction.
- **Effect sizes, highest vs lowest comomentum quintile:**
  - Formation-year momentum return is **+2.59%/month** higher (t=2.45).
  - **Year-1 post-formation return is −1.06%/month** (t=−2.72).
  - **Year-2 return is −1.08%/month** (t=−2.75).
- **Crash frequency:** in the first 3 months after formation, the share of "bad" momentum days (return < −1%) is **8.4% when comomentum is low vs 22.5% when high**.
- Comomentum is persistent: the year-0 to year-1 correlation is 0.48.

**Brown, Howard & Lundblad (2022, *RFS* 35(7):3231–3271), "Crowded trades and tail risk"** — MODERATE. Uses position-level long US equity holdings of about **1,500 hedge funds, 2004–2016**, with crowdedness measured partly as holdings ÷ ADV ("days-ADV"). They find a sizable return difference between high- and low-crowdedness portfolios, distinct from standard factors. **Funds with more crowding exposure had larger drawdowns during industry distress** — [IDEAS](https://ideas.repec.org/a/oup/rfinst/v35y2022i7p3231-3271..html); [Swedroe/ETF.com](https://www.etf.com/sections/index-investor-corner/swedroe-crowded-trades-tail-risk).

**Barroso, Edelen & Karehnke (2022, *JFQA* 57(4):1313–1342), "Crowding and tail risk in momentum returns"** — MODERATE, and a counter-evidence check. In their model, crowding creates tail risk only if arbitrageurs ignore feedback. Empirically, **institutional-holdings crowding proxies are *negatively* related to expected crash risk**. They conclude crowding "as a stand-alone source of tail risk faces both theoretical and empirical doubt" — [IDEAS](https://ideas.repec.org/a/cup/jfinqa/v57y2022i4p1313-1342_3.html).

**Khandani & Lo (2007/2011), "What happened to the quants in August 2007?"** — STRONG as a forensic case study (n = 1 episode). All figures from [NY Fed conference version](https://www.newyorkfed.org/medialibrary/media/research/conference/2007/liquidity/Khandani_Lo.pdf).
- **Daily returns:** an unlevered Lehmann/Lo–MacKinlay contrarian strategy returned **−1.16%, −2.83% and −2.86% on Aug 7, 8 and 9, 2007**.
  - Cumulative **−6.85%**, a **7.6σ** move against its 2006 daily σ of 0.52%.
  - On **Aug 10 it rebounded +5.92%** (11.4σ). The **whole week netted −0.43%**.
- **Index backdrop:**
  - Losses on Aug 7–8 came "despite relatively little movement in fixed-income and equity markets".
  - On Aug 9 the **S&P 500 lost nearly 3%**.
  - Funds that de-risked along the way locked in losses and missed the reversal.
- **Dynamics:** most of the unwind and de-leveraging happened over **Aug 7–9**, followed by a "significant—but not complete—reversal". They read it as a forced liquidation, not a breakdown of the strategy's economics.
- **Contributing factors:** asset growth in long/short and 130/30 strategies, falling profitability, **higher leverage** (8:1 is possible), and unawareness of crowding.

**Case studies of leader-led unwinds in a down or flat tape** (practitioner/press; descriptive only)
- **July 2024:**
  - After the soft CPI print on July 11, the **Russell 2000 rose 11.5% while the Nasdaq-100 fell ~5% over five sessions**, the largest 5-day divergence on record.
  - The **S&P 500 lost only ~1% from July 11–18**, then fell further as the tech selloff deepened, reaching a low on Aug 5 (CME puts it at about −10%) — [CME Group](https://www.cmegroup.com/openmarkets/equity-index/2024/Behind-the-Sector-Rotation-Away-from-Tech.html).
  - Over July, the Nasdaq-100 Dorsey Wright Momentum index fell **−8.3%**, and small caps beat the NDX by **11.7%**, the most since April 2002 — [Nasdaq July 2024 scorecard](https://www.nasdaq.com/articles/index-monthly-scorecard-july-2024).
  - Equal-weight S&P beat cap-weight during the rotation — [Bloomberg wrap via BNN](https://bnnbloomberg.ca/investing/2024/07/10/sp-futures-signal-endurance-to-rate-fueled-rally-markets-wrap).
- **Jan 27, 2025 (DeepSeek):**
  - Nvidia fell 17%, a record one-day loss of roughly $590–600bn.
  - In Reuters' afternoon update the **S&P 500 was −1.9%, the Nasdaq −3.7%, and the Dow +0.5%** — [Reuters via Tiger](https://www.itiger.com/hans/news/2506845515); [Invesco](https://www.invesco.com/apac/en/institutional/insights/market-outlook/market-experts-deep-dive-deepseek.html).
- **Nov 9, 2020 (Pfizer vaccine; a momentum crash *in an up tape*):**
  - MSCI: the momentum factor lost **nearly 5% in one day**, wiping out most of its prior nine months' gains. Semis and profitability slumped; airlines and restaurants jumped — [MSCI](https://www.msci.com/research-and-insights/blog-post/are-momentum-wings-finally-starting-to-melt).
  - Risk.net called it the sharpest momentum-to-value rotation on record — [Risk.net](https://www.risk.net/investing/quant-investing/7714536/vaccine-rally-showed-value-flawed-as-momentum-hedge-quant-says).
  - The S&P 500 gained about 9% in November 2020 — [Benzinga](https://www.benzinga.com/news/earnings/20/12/18597896/tuesdays-market-minute-novembers-i-want-out-trade).

### Inferences
- **The index and the factor diverge.** In the academic record the *factor* crash (momentum −40% to −75% in a month) coincides with **index rallies** after deep drawdowns (1932, 2009, Nov 2020). So a momentum-unwind signal is **not** by itself bearish for the index over 1–20 days. In DM's panic state it is, if anything, a marker of a violent *rebound* regime with very high two-way volatility.
- **The monitor's "Crowded-leadership unwind" label (SPY down, leaders ≤ −1.5σ, RSP > SPY) describes a different phenomenon.** It is de-grossing of crowded longs in a falling tape (July 2024, DeepSeek). Peer-reviewed evidence on the *index's* forward 1–20-day path after such days is **essentially absent**. The evidence that does exist (Khandani & Lo, n=1) says pure liquidity unwinds are **front-loaded (2–3 sessions) and partially mean-revert quickly**. July 2024 shows the opposite risk: the unwind preceded a further ~8–10% index drawdown, partly because of a separate shock (the yen-carry unwind, which the monitor tracks via USD/JPY).
- **Duration:**
  - Liquidity- or deleveraging-driven unwinds took **days** (Aug 2007: 3 days of losses, then 1 day of rebound).
  - DM-type momentum crashes are **months**, and the factor's underperformance can persist **years** (1932–39, 2009–13).
  - Comomentum-implied reversal plays out over **12–24 months**.
  - None of these durations is an *index* duration.
- **Crowding measures that can be built from data the monitor already has:**
  - Comomentum: needs ~14 months of constituent weekly returns, which ~440 calendar days covers.
  - Realised variance of MTUM−USMV (Barroso & Santa-Clara).
  - The DM panic state, from S&P index history already fetched since 1990.
  - Holdings-based crowding (13F, hedge-fund positions) is out of reach and also contested (Barroso, Edelen & Karehnke).

### Gaps
- Barroso & Santa-Clara's exact moments (Sharpe before and after, kurtosis, worst month) were not verified this session.
- No systematic study was found on **forward S&P 500 returns at 1–20 days after a momentum or crowding unwind day *in a down tape***. This would need an in-house event study: Ken French daily UMD, or MTUM−USMV since 2013, conditioned on SPY < 0 and RSP > SPY.
- The direction of Brown, Howard & Lundblad's crowded-minus-uncrowded *average* return was not confirmed from the full text.
- The S&P 500 equal-weight return and A/D on 2025-01-27 were not confirmed. The DeepSeek index figures above are intraday (afternoon), not closes.

---

## Q4. Market-cap concentration and mega-cap-driven moves: index returns when top-10 weight is high; equal-weight vs cap-weight divergence

### Takeaway
No peer-reviewed study was found showing that high top-10 concentration predicts **1–20-day** index returns. The available evidence is **industry research at multi-year horizons**. After concentration peaks, equal-weight has tended to beat cap-weight (S&P DJI), and the "bottom 490" beat the top 10 in 84% of 5-year windows when the top 10 was ≥30% (Hartford). Concentration can stay elevated for years: stepping aside at the late-2020 record would have missed large gains. Concentration explains *how* the index moves, mechanically, rather than *where* it goes next.

### Cited Findings
- **Hartford Funds (industry; WEAK–MODERATE, 5-year horizon):** using the market-cap-weighted top 500 from 1964–2025, when the **top-10 share is ≥30%, the "bottom 490" outperformed the top 10 in 84% of the following 5-year periods**. Rising concentration and valuations often preceded higher volatility and bigger drawdowns *for the largest stocks* — [Hartford Funds](https://www.hartfordfunds.com/insights/market-perspectives/strategic-beta-etfs/the-messy-aftermath-of-concentrated-markets.html).
- **S&P Dow Jones Indices (industry; MODERATE, multi-year):** after concentration peaks, the **S&P 500 Equal Weight has typically outperformed** cap-weight, the post-tech-bubble period being the example. Concentration (a size-adjusted HHI) tends to **mean-revert** in most sectors — [S&P DJI](https://spglobal.com/spdji/en/research/article/concentration-within-sectors-and-its-implications-for-equal-weighting).
- **Morningstar:**
  - The top 10 held **37.3%** of US market value at the 1932 peak.
  - Top-10 weight passed its dot-com level by **late 2020**, and an investor who stepped aside then "would have missed several years of exceptional gains". Concentration is a poor timing signal — [Morningstar](https://www.morningstar.com/stocks/stock-market-concentration-has-surpassed-its-1930s-peak-should-investors-worry).
- **Morgan Stanley Counterpoint Global (Mauboussin):**
  - Fundamentals can justify concentration: in **2014–2023 the top 10 averaged 19% of market cap but 47% of economic profit**.
  - Dimson–Marsh–Staunton data: **top-10 weight was 38% at end-1899** and bottomed at **7.9% in 1995** — [Morgan Stanley PDF](https://www.morganstanley.com/im/publication/insights/articles/article_stockmarketconcentration.pdf).
- **Aalto University master's thesis** (WEAK; unpublished): on HHI/Gini concentration in the S&P 500, NASDAQ-100 and STOXX 600 over 1–5 year horizons, higher concentration relates to **lower future excess returns and higher future volatility** — [Aalto](https://aaltodoc.aalto.fi/bitstreams/7190e64c-fad2-4c35-991c-e59ed6a272fd/download).
- **Goldman (2020)** argued narrow leadership historically ends in a "catch-down" of the leaders to the laggards — [ai-CIO](https://www.ai-cio.com/news/bad-breadth-5-giants-market-dominance-grim-sign-goldman-says/). As noted in Q1, the same team took the opposite view in 2018 and 2021.
- **Mechanical channel (descriptive):** on 2025-01-27 a single stock (NVDA −17%) drove the S&P down about 1.9% intraday while the Dow rose — [Reuters via Tiger](https://www.itiger.com/hans/news/2506845515). In July 2024, equal-weight outperformed while the NDX fell — [CME](https://www.cmegroup.com/openmarkets/equity-index/2024/Behind-the-Sector-Rotation-Away-from-Tech.html).

### Inferences
- With the top 10 at roughly 35–40% of the S&P 500 (Hartford/Morningstar range), a 3-session cap-weighted index move can be dominated by fewer than 10 names. The monitor's "Mega-cap driven = top holdings ≥60% of the move" rule is a sound **attribution** statement, not a forecast. Its 60% cut-off is a convention.
- **RSP > SPY during an index decline** means the average stock is holding up better than the giants. That is *better* breadth than the headline suggests. On the continuation evidence (Zaremba et al.) this is mildly *less* bearish for broad participation than a broad liquidation. There is no peer-reviewed short-horizon evidence on what it implies for the cap-weighted index.
- Concentration claims should be confined to the horizon where evidence exists (multi-year relative EW-vs-CW performance), and labelled as industry research.

### Gaps
- No peer-reviewed paper found on aggregate index-level concentration → future index returns or drawdowns at any horizon. Searches surfaced industry, sector-level or ownership-concentration work only.
- The Dimson–Marsh–Staunton (UBS Yearbook 2024/2025) chapter on whether concentration predicted subsequent market returns was not retrievable in full.
- No study found on short-horizon (1–20d) index returns conditional on daily EW−CW divergence.

---

## Q5. What can a monitor responsibly claim? Which thresholds have evidence vs are convention, and concrete implementable upgrades

### Takeaway
Most of the monitor's breadth and factor thresholds are **conventions**, not evidence-backed cut-offs. These include −1.5σ, Zweig 0.40/0.615, Lowry 90%, washout ≤X% above the 20-DMA, and 60% mega-cap share. The monitor can responsibly *describe* the move: how broad, how forced, what got sold, who drove the index. It can flag one **evidence-backed state** for the momentum factor: the Daniel–Moskowitz panic state, plus comomentum and Barroso–Santa-Clara volatility. It should **not** attach forward 1–20-day index return claims to breadth or factor labels unless they come from its own point-in-time backtest with confidence intervals.

### Cited Findings
- **Evidence-backed state variables:**
  - **Panic state:** 24-month market return < 0 plus high 126-day variance forecasts momentum crashes. Bear-state WML alpha is about −6 bp/month, and the panic-state mean is ~59%/yr below normal — [Daniel & Moskowitz](https://www.nber.org/system/files/working_papers/w20439/w20439.pdf).
  - **Momentum's own realised variance** forecasts its risk — [Barroso & Santa-Clara](https://ciencia.ucp.pt/en/publications/momentum-has-its-moments/).
  - **Comomentum** high vs low: −1.06%/month in year 1, and bad-day share 22.5% vs 8.4% — [Lou & Polk](https://personal.lse.ac.uk/polk/research/hhab117.pdf).
- **Breadth with peer-reviewed predictive content (continuation, monthly):** Zaremba et al. 2021 — [IDEAS](https://ideas.repec.org/a/eee/ecmode/v97y2021icp348-364.html). Breadth-type sentiment has little short-horizon predictive power — [CXO on Brown & Cliff](https://www.cxoadvisory.com/sentiment-indicators/does-investor-sentiment-give-trading-signals). Index 52-week-high nearness predicts positively — [Li & Yu](https://academicnewsletter.sufe.edu.cn/info/356820).
- **Practitioner conventions with tiny samples:**
  - Zweig: about 20 NYSE signals since 1945; 6 since 2010 on a broad universe; sensitive to the universe — [thetrading.tools](https://www.thetrading.tools/concepts/zweig-breadth-thrust).
  - Lowry: NYSE volume and points, with clusters at bottoms — [Ritholtz/Desmond](https://ritholtz.com/2011/02/qa-paul-desmond-of-lowrys-part-ii).
  - % above MA levels — [LuxAlgo](https://www.luxalgo.com/library/concept/percent-stocks-above-20-50-200-day-ma/).
- **Pure liquidity unwinds are front-loaded and partially reverse:** −6.85% over 3 days, then +5.92% the next day — [Khandani & Lo](https://www.newyorkfed.org/medialibrary/media/research/conference/2007/liquidity/Khandani_Lo.pdf).

### Inferences
**Evidence-strength scorecard for the monitor's components**

| Component | Use it as | Evidence for 1–20d index prediction | Threshold status |
|---|---|---|---|
| % above 20/50/200-DMA | Participation description | WEAK (practitioner only); index-level MA signals are STRONG at monthly (Neely et al.), but that is the index, not the % of members | Washout and overbought levels are convention |
| 52-wk new highs/lows (closing) | Internal-damage description | WEAK; index-level 52-wk-high nearness is MODERATE positive (Li & Yu) | NL > NH is convention |
| Adv/Dec, % of members down, median vs index | Breadth of the move | MODERATE continuation at monthly (Zaremba); weak at 1–20d (Brown & Cliff) | None evidence-based |
| Lowry 90% up/down-volume days | Forced or indiscriminate flow | FOLKLORE/WEAK; NYSE-calibrated | 90% (and 80%×2) are Desmond conventions |
| Zweig thrust | Rare recovery marker | WEAK (n≈20 since 1945); NYSE-calibrated, universe-sensitive | 0.40/0.615/10d are Zweig conventions |
| Factor spreads (MTUM−USMV, SPHB−SPLV, VUG−VTV, SMH−SPY, IWM−SPY, cyc−def) | What got sold | STRONG only for the *momentum factor's* own crash risk (DM, B&SC, Lou–Polk); not for the index | −1.5σ is convention |
| RSP−SPY | Mega-cap vs average stock | Multi-year industry evidence only (S&P DJI, Hartford) | Sign test is descriptive |
| Top-holdings share of the move | Attribution | n/a (mechanical identity) | 60% is convention |

**Concrete, implementable upgrades.** These are deterministic and use data already fetched (S&P index since 1990, ~440 days of constituent closes and volumes, ETF closes).

1. **Add a Daniel–Moskowitz "Momentum-crash regime" flag** (evidence: STRONG for the factor).
   - Definition: `I_B = SPX 504-session (≈24-month) total return < 0`, and `σ²_126` = 126-session daily variance of SPX log returns.
   - Flag "panic state" when I_B = 1 and σ²_126 is in the top tercile of its 1990+ history.
   - Copy: "Momentum/low-quality reversal risk elevated. Historically, crashes in this state happened on sharp index *rebounds*, led by past losers."
   - Keep this separate from the crowded-unwind label. The two describe opposite index directions.
2. **Split the current "Crowded-leadership unwind" into two labels so the direction is explicit:**
   - **(a) "Crowded-long de-grossing" (current rule):** SPY < 0, any leader spread ≤ −1.5σ, RSP > SPY. Copy: "descriptive; no peer-reviewed short-horizon index forecast."
   - **(b) "Short-squeeze / junk rally (momentum crash)":** SPY > 0, MTUM−USMV ≤ −1.5σ, and SPHB−SPLV ≥ +1.5σ or IWM−SPY ≥ +1.5σ. This is the DM pattern (Nov 9 2020, Mar–May 2009).
   - Today a factor crash in an up tape falls through to "Mixed".
3. **Add a "market-neutral liquidity shock" tell** (Khandani & Lo mechanism).
   - Fire when |MTUM−USMV z| ≥ 3 *and* |SPY z| < 1 over the window.
   - Copy: "Factor dislocation without an index move. The 2007 precedent unwound over 2–3 sessions and partly reversed the next day (n=1)."
   - Show the **next-session spread return** once available, to confirm or deny reversal.
4. **Compute a constituent-based comomentum gauge** (Lou & Polk; evidence STRONG for the factor's 1–2-year forward returns and bad-day frequency).
   - At each month-end, rank S&P members on their t−12 to t−1 month return. Take the top and bottom deciles.
   - Compute the average pairwise correlation of their **52 weekly market-model residuals** (residual vs SPY, as a proxy for the FF3 residual).
   - Report its percentile vs the stored history of *this* gauge. Store it going forward; historical values are survivorship-biased (Q2).
   - It fits in the existing ~440-day history (≈52 weeks of returns plus skip-month), but the current list makes deep history biased. Label it "crowding (approx.)".
5. **Barroso–Santa-Clara factor-vol context:** show the realised 126-day volatility of each factor spread and its percentile. Use this σ, rather than only the 60-day pre-window σ, as a second z denominator, so a −1.5σ day in a calm regime isn't over-read. Express thresholds as **percentiles of the spread's own history** (MTUM/USMV/SPHB/SPLV ETF histories go back roughly a decade; inception dates to be checked) rather than a fixed −1.5σ.
6. **Universe honesty for Zweig and Lowry:**
   - Rename to "Zweig-style thrust (S&P 500 members)" and "90% day (S&P 500 member volume)".
   - Show the EMA's **peak value and distance to 0.615** (the 0.612 near-miss problem), and the count of names in the denominator.
   - For Lowry, also require Desmond's sequencing: a 90% *up* day **after** a cluster of 90% down days, with two back-to-back 80% up days as an alternative.
   - State that the thresholds were calibrated on NYSE issues, not S&P 500 members.
7. **Survivorship controls for breadth history** (Q2):
   - Ingest a membership-changes table and compute breadth on point-in-time members.
   - Until then, keep the existing policy of "no historical percentiles for breadth".
   - Show "n with full 252-day history" beside new highs/lows and % above the 200-DMA.
8. **Only forward-return claims the monitor itself estimated:**
   - If any label is to carry "what happened next", run an event study on the 1990+ S&P history: forward 1/5/10/20-day SPX returns, first-passage and drawdown, with **Wilson CIs and n**, against the unconditional base rate. The existing k-NN analog engine already follows this pattern.
   - Breadth-conditioned studies need point-in-time membership, or an exchange-wide A/D series.
   - Where n < 30, show the n and say "too few episodes to estimate".
9. **Concentration:**
   - Add the top-10 weight and the **share of the window's index move from the top 10**. The latter is already partly in the "mega-cap driven" rule.
   - Add an "equal-weight gap" (RSP−SPY window return).
   - Restrict narrative to attribution ("the index fell X%; the median member fell Y%; the top 10 contributed Z pp").
   - Do not imply that a high concentration level predicts the next 1–20 days. The evidence is multi-year and industry-sourced.
10. **Copy rules** (consistent with the project's "no invented facts" principle):
    - Every label shows the rule that fired and the raw numbers.
    - Every predictive sentence names its evidence tier (peer-reviewed / practitioner / convention) and its horizon.
    - Momentum-crash language must state that crashes historically **coincided with index rebounds**.

### Gaps
- Inception dates and history lengths of MTUM (MSCI USA Momentum), USMV, SPHB and SPLV, needed for percentile-based thresholds, were not verified this session.
- No academic calibration exists for "−1.5σ over a 3-session window" on ETF factor spreads. It would need in-house calibration, e.g. the frequency and forward behaviour of such days since about 2013.
- The forward index behaviour after "crowded-long de-grossing" days (SPY↓, leaders ≤ −1.5σ, RSP > SPY) is unknown. It is the single most decision-relevant missing piece, and would require the in-house event study in upgrade 8.
