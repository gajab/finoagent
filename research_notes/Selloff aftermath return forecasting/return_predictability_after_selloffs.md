# Short-horizon (1–20 day) S&P 500 index return predictability after sharp selloffs: reversal vs continuation, and regime dependence

Scope note: these notes cover index-level evidence (S&P 500 / national indexes), not single-stock reversal, except where the cross-sectional work tells us something about the market state (Nagel 2012; Daniel–Moskowitz 2016). Source-quality flags: **[PR]** = peer-reviewed journal; **[WP]** = NBER / central-bank / SSRN working paper; **[PRAC]** = practitioner, blog or aggregator (CXO Advisory, Alpha Architect, etc.); **[THESIS]** = student thesis. Where a number comes only from a secondary summary, it is flagged.

---

## Q1. Do daily/weekly S&P 500 returns reverse or continue after large declines, and how has index autocorrelation changed over time?

### Takeaway
Index-level serial dependence has changed sign. It was **positive** from the 1950s through the 1980s/1990s (daily AR(1) around +0.05), partly because of stale prices. Since about 2000 it has been **significantly negative** in the S&P 500 and in most of the 20 major world indexes studied. So in the modern era the baseline short-horizon (1–5 day) tendency is mild **reversal**. Older event studies of index "shocks" (1989–1998) found **continuation**, so any analog engine trained on pre-2000 history mixes two opposite regimes.

### Cited Findings
- **Baltussen, van Bekkum & Da (2019, JFE) [PR]**
  - Sample and headline: 20 major indexes in 15 countries, from 1951 or each index's start date. Index serial dependence "switches to negative since the 2000s", and the change is statistically significant and economically meaningful in most markets. — [Baltussen, van Bekkum & Da, JFE accepted manuscript](https://personal.eur.nl/vanbekkum/2018%20JFE%20BaltussenVanBekkumDa.pdf); [author PDF](https://academicweb.nd.edu/~zda/Indexing.pdf)
  - Daily AR(1): coefficients "fluctuat[ed] around 0.05 from 1951", declined steadily from the 1980s, switched sign during the 2000s and have "remained negative ever since". Weekly AR(1) "switched sign even before 2000". — [Baltussen et al. (author PDF)](https://academicweb.nd.edu/~zda/Indexing.pdf)
  - S&P 500 effect size (Table 2): the multi-period autocorrelation MAC(5) uses linearly declining weights on daily lags 1–4. It moved from **+0.050 before 3 Mar 1999 to −0.141 after** (t-diff −4.30). The exponentially weighted EMAC(5) moved from +0.014 to −0.048 (t-diff −4.46). — [Baltussen et al.](https://academicweb.nd.edu/~zda/Indexing.pdf)
  - S&P 500 break dates (Table 3): the CUSUM break date in MAC(5) is **14 Aug 1987**, and S&P futures started 22 Apr 1982. Pre-futures intercept b1 = +0.085, futures-introduction dummy b2 = −0.165. S&P futures MAC(5) averaged −0.091, and the SPY ETF (from 29 Jan 1993) averaged −0.087. — [Baltussen et al.](https://academicweb.nd.edu/~zda/Indexing.pdf)
  - Economic size: trading against MAC(5) after 2 Mar 1999 earns an annualized **Sharpe of 0.63 across all indexes (0.67 for the S&P 500 alone)**, against an average buy-and-hold index Sharpe of 0.36. The authors warn that the strategy "requires frequent rebalancing" and "might not be exploitable to many investors after accounting for transaction costs". — [Baltussen et al.](https://academicweb.nd.edu/~zda/Indexing.pdf)
  - Robustness and "out-of-sample": the paper does not run a forecasting out-of-sample test. Its robustness evidence is that the result is not just a time trend:
    - The effect holds across indexes (break dates line up with each index's futures introduction).
    - Futures and ETFs show negative serial dependence from their own inception.
    - A 1% increase in indexing lowers MAC(5) by about 0.031 (panel coefficient −3.131, t = −2.90).
    - Results hold after controls for 21-day realized volatility, detrended volume, past 21-day return and the TED spread. — [Baltussen et al.](https://academicweb.nd.edu/~zda/Indexing.pdf)
  - The unreported coefficients on volume and on last month's volatility are "in line with those in Campbell et al. (1993) and Nagel (2012)": more volume and more volatility go with more negative serial dependence (more reversal). — [Baltussen et al.](https://academicweb.nd.edu/~zda/Indexing.pdf)
  - The paper excludes Oct 1987 and Sep 2008 when locating break dates, and says removing them elsewhere does not change the results. — [Baltussen et al.](https://academicweb.nd.edu/~zda/Indexing.pdf)
- **Lo & MacKinlay (1988, RFS) [PR]**, "Stock market prices do not follow random walks". This is the classic variance-ratio rejection of the random walk for weekly index returns, published in RFS 1(1):41–66. I did not re-verify the exact autocorrelation magnitudes in this pass. — [IDEAS record via Lo–MacKinlay 1990 listing](https://ideas.repec.org:443/a/oup/rfinst/v3y1990i2p175-205.html)
- **Lo & MacKinlay (1990, RFS) [PR]**
  - Weekly *portfolio* returns are "strongly positively autocorrelated" even though individual stocks show negative autocorrelation. The positive index autocorrelation comes from cross-autocorrelations, with large stocks leading small ones.
  - Overreaction is not the sole source of contrarian profits. — [Lo & MacKinlay 1990 (MIT page)](https://web.mit.edu/Alo/www/Papers/lo-mackinlay-90b.html); [NBER w2977](https://www.nber.org/system/files/working_papers/w2977/w2977.pdf)
- **Ahn, Boudoukh, Richardson & Whitelaw (2002, RFS) [PR]**, covering 24 futures contracts in 15 countries.
  - Cash-index autocorrelations are positive while the matching **futures autocorrelations are near zero**, and the gap is largest in low-volume periods.
  - The authors read this as a microstructure (stale-price / nonsynchronous trading) explanation, not a behavioral one. — [NBER w7214 "Behavioralize This!"](https://www.nber.org/papers/w7214)
- **Lasfer, Melnik & Thomas (2003, J. Banking & Finance) [PR]**, using daily indexes from 39 exchanges over 1989–1998.
  - After large shocks, prices **continued** in the direction of the shock (positive abnormal returns after positive shocks, negative after negative), for **up to about 10 days**.
  - Post-shock momentum was larger in emerging and less-liquid markets and "clearly weaker in the late 1990s". This rejects overreaction for that sample. — [Lasfer, Melnik & Thomas (Swansea repository record)](https://cronfa.swansea.ac.uk/Record/cronfa2231); [Haifa CRIS](https://cris.haifa.ac.il/en/publications/short-term-reaction-of-stock-markets-in-stressful-circumstances/)
- **Dolvin & Pyles (2009, *Financial Decisions*) [PR, lower-tier journal]**
  - At the overall-market level, days after large declines (below −1%) "tend to exhibit return reversal".
  - Days after large gains (above +1%) generally continue, but more weakly.
  - The authors call the very-short-horizon market evidence "less consistent" than prior studies suggest. — [Dolvin & Pyles (Butler University record)](https://butler.elsevierpure.com/en/publications/daily-stock-returns-momentum-reversal-or-both/)
- **Cox & Peterson (1994, J. Finance) [PR]**, single stocks only. Reversals after large one-day declines are explained by bid-ask bounce and market liquidity, not overreaction, and stocks with big one-day drops underperform over longer horizons. This is a microstructure caution, not index evidence. — [Cox & Peterson record (SUFE newsletter)](https://academicnewsletter.sufe.edu.cn/info/362229)
- **Joseph & Mazouz [PR, details unverified]**, 39 national indexes. They report continuation after both positive and negative shocks, and overreaction after large negative shocks. This came from a search-engine summary and I could not open the paper. — [search summary pointing to Coventry repository](https://pureportal.coventry.ac.uk/en/publications/testing-for-overreaction-and-return-continuations-in-stock-price--2/)
- **Kim, Shamsuddin & Lim (2011, J. Empirical Finance) [PR]**, monthly DJIA 1900–2009.
  - Predictability is time-varying and driven by market conditions, consistent with the adaptive-markets hypothesis.
  - During **market crashes, no statistically significant predictability** was found.
  - Returns were more predictable in economic or political crises, and there was "little predictability" after 1980. — [IDEAS record](https://ideas.repec.org/a/eee/empfin/v18y2011i5p868-879.html); [CXO summary [PRAC]](https://www.cxoadvisory.com/big-ideas/variation-in-long-run-stock-market-predictability/)

### Inferences
- The conflict between continuation (Lasfer et al., 1989–1998) and reversal (Dolvin & Pyles; Baltussen et al. post-1999) is best explained by era. Pre-2000 index data are contaminated by stale prices (Ahn et al.), and index products later flipped the sign (Baltussen et al.). **An analog library that pools 1950–1999 with 2000–present will blend a positive-autocorrelation regime with a negative one**, biasing short-horizon (1–5 day) analog outcomes toward continuation relative to what applies today.
- The modern daily reversal effect is small per day (MAC(5) ≈ −0.14 is a weighted correlation). It is most useful for 1–5 day horizons and decays quickly. It does not on its own imply large 20-day rebounds.

### Gaps
- I found no peer-reviewed paper that directly reports S&P 500 forward 1/5/10/20-day returns after an N-σ **3-day** decline split by era, with out-of-sample validation. Connors-style "3-day decline" studies are practitioner material and were not verified here.
- Exact Lo–MacKinlay (1988) weekly autocorrelation magnitudes were not re-verified in this pass.

---

## Q2. How does trend conditioning (above/below 200-day MA; time-series momentum) change post-selloff outcomes?

### Takeaway
There is solid peer-reviewed evidence that trend state carries information. Returns after "sell" signals (price below long MAs) are lower and more volatile, and slow vs fast momentum disagreement (a "correction" in an uptrend vs a "bear") is informative. But much of the classic MA-rule profitability **weakened or vanished out-of-sample** (Sullivan–Timmermann–White), and the gains are concentrated in recessions and bear states. "Dips get bought in uptrends, continue in downtrends" is consistent with this literature but is not established at the 1–20 day horizon by a peer-reviewed out-of-sample test that I could find.

### Cited Findings
- **Brock, Lakonishok & LeBaron (1992, J. Finance) [PR]**: 26 simple MA and trading-range rules on about 100 years of DJIA data, with predictive power and abnormal returns. This is per a secondary summary; the sell-signal volatility detail is under Gaps. — [secondary summary (SUFE / bootstrap literature)](https://academicnewsletter.sufe.edu.cn/info/361689)
- **Sullivan, Timmermann & White (1999, J. Finance) [PR]**: applied White's Reality Check across about 8,000 rules.
  - In the original sample, the best rule's performance is not explained by data snooping.
  - **Out-of-sample 1987–1996, the best rules earned a statistically insignificant 8.63%/yr before costs.** This figure is from a secondary source.
  - Later work calls the Reality Check conservative, which led to Hansen's SPA test. — [STW (LSE Research Online)](https://researchonline.lse.ac.uk/id/eprint/119144); [FMG discussion paper dp303](https://www.fmg.ac.uk/sites/default/files/2020-11/dp303.pdf); [Data-snooping summary [PRAC]](https://data-snooping.martinsewell.com/)
- **Neely, Rapach, Tu & Zhou (2014, Management Science) [PR]**: technical indicators (MA, momentum, OBV) have statistically and economically significant **in-sample and out-of-sample** predictive power for the monthly equity premium, matching or beating macro variables.
  - The longest MA indicator earned an annualized utility gain of 2.98% overall, **0.89% in expansions vs 14.9% in recessions**. These figures come only from the CXO summary. — [Washington University profile](https://profiles.wustl.edu/en/publications/forecasting-the-equity-risk-premium-the-role-of-technical-indicat/); [St. Louis Fed WP 2010-008](https://files.stlouisfed.org/files/htdocs/wp/2010/2010-008.pdf); [CXO summary [PRAC]](https://www.cxoadvisory.com/fundamental-valuation/technical-boost-to-fundamental-stock-market-forecasting)
- **Moskowitz, Ooi & Pedersen (2012, JFE) [PR]**: in 58 liquid futures (equity index, FX, commodity, bond), returns persist over **1–12 months** and **partially reverse over longer horizons**. The authors attribute this to initial under-reaction followed by delayed over-reaction. — [SSRN abstract](https://papers.ssrn.com/abstract=2089463)
- **Goulding, Harvey & Mazzoleni (JFE 2023 "Momentum turning points"; FAJ 2023 "Breaking bad trends") [PR]**
  - The market is classified into four states using a slow (12-month) and a fast (1- or 2-month) trailing return:
    - **Bull**: both up.
    - **Correction**: slow up, fast down.
    - **Bear**: both down.
    - **Rebound**: slow down, fast up.
  - Corrections and rebounds "carry predictive information about subsequent" returns. The dynamic speed choice (no look-ahead) beat static trend following. In the FAJ version the worst drawdown was −13.2% vs −18.8% for static 12-month trend, over 1990–2022.
  - Turning-point frequency rose after the GFC (2009–2019), which hurt trend following. — [FAJ "Breaking Bad Trends" (Duke PDF)](https://people.duke.edu/~charvey/Research/Published_Papers/P167_Breaking_bad_trends.pdf); [JFE record (SUFE)](https://academicnewsletter.sufe.edu.cn/info/354804); [AEA 2021 paper](https://benny.aeaweb.org/conference/2021/preliminary/paper/492Ds6fk)
  - A search summary said "state-dependent analysis elected slower-speed momentum after Correction months and faster after Rebound months". That points to corrections inside uptrends tending to resolve in the slow-trend direction. I could not verify the per-state US mean returns from the full text. — [CXO summary [PRAC]](https://www.cxoadvisory.com/technical-trading/mitigating-impact-of-price-turning-points-on-trend-following/)
- **Han, Zhou & Zhu (2016, JFE) [PR]**: a cross-sectional "trend factor" combining MAs from 3 to 1,000 days. It substantially outperforms the separate short-term reversal, momentum and long-term reversal factors, and was positive in 2008 when the market and momentum crashed. This is cross-sectional, not index timing. — [IDEAS record](https://ideas.repec.org/a/eee/jfinec/v122y2016i2p352-375.html); [CXO summary [PRAC]](https://www.cxoadvisory.com/technical-trading/trend-factor-and-stock-returns)
- **Faber (2007, J. Wealth Management) [PRAC-leaning; journal is practitioner-oriented]**: a monthly 10-month SMA timing rule (in when above, cash when below). The paper's own claim is that drawdown was cut from about 46% to under 10% for a diversified portfolio. Monthly signals miss intra-month crashes. Details are from secondary sources. — [QuantConnect discussion [PRAC]](https://www.quantconnect.com/forum/discussion/8285/a-quantitative-approach-to-tactical-asset-allocation-gtaa-5-with-simplemovingaverage/); [CXO summary [PRAC]](https://www.cxoadvisory.com/technical-trading/long-term-outperformance-from-trends-defined-by-moving-averages)

### Inferences
- The trend feature in the Monitor (distance to the 200-day MA) is well supported as a *state* variable. Its value is more about **risk (dispersion and left tail) and the recession/bear regime** than about reliable 1–20 day mean returns.
- A **categorical slow/fast momentum state** (Correction vs Bear) is the closest academic analogue to "dip in an uptrend vs drop in a downtrend". The Monitor's continuous kNN distance may blur it, because a −2σ 3-day drop above the 200-day MA and the same drop below it can be close in Euclidean space if other features match.
- Expect the trend-conditioned difference to be modest and unstable out-of-sample (Sullivan–Timmermann–White; post-GFC turning-point increase). Present it with honest confidence intervals rather than as a rule.

### Gaps
- Brock–Lakonishok–LeBaron's widely cited finding, that returns after MA "sell" signals are negative and more volatile than after "buy" signals, was not verified from the primary text in this pass. Confirm it before citing.
- I found no peer-reviewed study testing "3-day selloff × above/below 200-day MA → forward 5/10/20-day S&P returns" with an out-of-sample split.
- Per-state mean returns from Goulding–Harvey–Mazzoleni (JFE) were not extracted from the full text.

---

## Q3. Volatility clustering and the leverage effect: what happens to the return distribution and risk-return trade-off after large down moves?

### Takeaway
After large down moves, **future volatility is reliably higher**: negative returns and negative semivariance and jumps raise future volatility more than positive ones (GJR, HAR-RV, Patton–Sheppard). This is among the most robust and out-of-sample-validated facts in the area. Expected returns **do not rise in proportion** to volatility (Moreira–Muir), so risk-adjusted forward returns are typically worse after volatility spikes. However, volatility-managed strategies built on this failed to beat buy-and-hold in feasible out-of-sample tests (Cederburg et al.).

### Cited Findings
- **Glosten, Jagannathan & Runkle (1993, J. Finance) [PR]**, "On the relation between the expected value and the volatility of the nominal excess return on stocks", 48(5):1779–1801. The GJR-GARCH leverage term applies only to negative past shocks: negative returns raise next-period conditional variance more than positive returns of the same size. — [citation via Nikkei realized-EGARCH paper (arXiv)](https://arxiv.org/pdf/2502.02695)
- **Corsi (2009, J. Financial Econometrics) [PR]**: the HAR-RV model forecasts realized volatility from daily, weekly and monthly RV components, capturing long memory. It is a standard benchmark. — [citation via BIST-100 HAR study](https://openaccess.bilgi.edu.tr/bitstreams/5e98bf0b-8920-4ca1-96bd-f375a173246d/download)
- **Patton & Sheppard (2015, REStat) [PR]**, using high-frequency S&P 500 data plus 105 stocks.
  - Future volatility is more strongly tied to the volatility of **past negative returns** (realized semivariance).
  - **Negative jumps raise, and positive jumps lower,** subsequent volatility.
  - Models exploiting this give **significantly better out-of-sample forecasts**, from 1 day to 3 months ahead. — [Duke PDF](https://public.econ.duke.edu/~ap172/Patton_Sheppard_Realized_Semivariance_7oct11.pdf); [Duke scholars record](https://scholars.duke.edu/publication/1082826)
- **Bollerslev, Litvinova & Tauchen (2006, J. Financial Econometrics) [PR]**, "Leverage and volatility feedback effects in high-frequency data", 4(3):353–384. Observed volatility asymmetries require multiple latent volatility factors. — [IDEAS record](https://ideas.repec.org/a/oup/jfinec/v4y2006i3p353-384.html)
- **Moreira & Muir (2017, J. Finance) [PR]**
  - Volatility timing raises Sharpe ratios "because changes in volatility are not offset by proportional changes in expected returns".
  - Volatility-managed portfolios take less risk in recessions and crises yet earn high average returns. Tested on market, value, momentum, profitability, ROE, investment, BAB and FX carry. — [NBER w22208](https://www.nber.org/papers/22208)
- **Cederburg, O'Doherty, Wang & Yan (2020, JFE) [PR]**, covering 103 equity strategies.
  - Volatility-managed portfolios show positive spanning-regression alphas, confirming Moreira–Muir.
  - However, they **do not systematically beat unmanaged versions in direct comparison**, and **feasible out-of-sample versions earn lower certainty-equivalent returns and Sharpe ratios**.
  - The authors blame structural instability in the scaling coefficients. — [Lehigh PDF](https://www.lehigh.edu/~xuy219/research/COWY.pdf); [Alpha Architect summary [PRAC]](https://alphaarchitect.com/does-portfolio-timing-based-on-volatility-signals-outperform-buy-and-hold/)
- **Barroso & Detzel (2020)**, as cited by others: volatility-managed strategies do not survive transaction costs. — [Bristol seminar abstract citing it](https://bristol.ac.uk/business-school/research/events/2022/business-school-research-seminar-finance---professor-victor-demiguel.html)
- **Daniel & Moskowitz (2016, JFE) [PR]**
  - Momentum crashes happen in "panic" states, after market declines and when market volatility is high, at the same time as **market rebounds**.
  - After major market declines, past-loser betas can exceed 3. This beta figure is from a secondary source.
  - The crashes are partly forecastable. — [NBER w20439](https://www.nber.org/papers/w20439); [Swedroe/ETF.com [PRAC]](https://www.etf.com/sections/index-investor-corner/swedroe-downside-momentum)

### Inferences
- For a Selloff Monitor, the **most defensible forecast after a selloff is about the distribution's width and left tail, not its mean**. Forward realized volatility, and the chance of a further −X% before +X%, should be reported next to the win rate. Win rate alone can mislead when dispersion jumps.
- Daniel–Moskowitz's "panic state" (market decline plus high volatility) is the regime where sharp index rebounds occur. It is also where forward volatility is highest, so the up-tail and the down-tail are both fat. This argues for reporting both tails of the forward-return quantiles.
- Do not convert higher post-selloff volatility into higher expected return. Moreira–Muir's core finding is that the expected-return response is less than proportional.

### Gaps
- Moreira–Muir's exact market-factor alpha and appraisal ratio were not re-verified here.
- I found no peer-reviewed estimate of conditional skewness of S&P 500 1–20 day returns given a prior N-σ decline.

---

## Q4. Evidence on returns after extreme days, VIX spikes, and "buy-the-dip"

### Takeaway
The strongest peer-reviewed evidence links **high volatility / VIX** to **stronger short-term reversal**. This holds for liquidity provision (Nagel 2012), in century-long daily index data (Sentana–Wadhwani 1992; LeBaron 1992), and with **high volume** (Campbell–Grossman–Wang 1993). Very high implied volatility has been followed by short-term gains (Giot), but the VIX *level alone* is a weak and unstable predictor. The VIX term structure and the variance risk premium (VRP) do better at monthly-to-quarterly horizons.

### Cited Findings
- **Nagel (2012, RFS 25(7):2005–2039) [PR]**
  - Short-term reversal returns proxy for the returns to liquidity provision.
  - Their expected return is **highly predictable with the VIX**: expected returns and conditional Sharpe ratios "rise sharply when the VIX is high" (for example 2007–09).
  - **Industry-portfolio reversal strategies**, which earn little on average, earn high returns when the VIX is elevated.
  - Nagel's mechanism is liquidity suppliers withdrawing in turmoil (constrained intermediaries). — [NBER w17653](https://nber.org/papers/w17653); [NBER PDF](https://www.nber.org/papers/w17653.pdf)
- **Sentana & Wadhwani (1992, Economic Journal 102(411):415–425) [PR]**, using about a century of daily US data.
  - Return autocorrelation is **positive at low volatility and negative at high volatility**.
  - **Negative autocorrelation is more pronounced after price declines.**
  - Their model has rational investors plus positive-feedback traders. — [IDEAS record](https://ideas.repec.org/a/ecj/econjl/v102y1992i411p415-25.html)
- **LeBaron (1992, J. Business 65(2):199–219) [PR]**: serial correlations vary over time and are **inversely related to volatility**, estimated with a GARCH extension. — [IDEAS record](https://ideas.repec.org/a/ucp/jnlbus/v65y1992i2p199-219.html); [Brandeis record](https://scholarworks.brandeis.edu/esploro/outputs/journalArticle/Some-Relations-Between-Volatility-and-Serial/9924036581101921)
- **Campbell, Grossman & Wang (1993, QJE 108(4):905–939) [PR]**
  - For stock indexes and large stocks, first-order daily autocorrelation **declines with trading volume**.
  - A price **decline on a high-volume day is more likely to be followed by a rise in expected return** than one on a low-volume day: market makers absorbing non-informational selling demand compensation. — [Harvard DASH PDF](https://dash.harvard.edu/bitstream/1/3128710/2/campbell_trading.pdf); [SSRN](https://papers.ssrn.com/abstract=297335)
- **Giot (2002 working paper; published 2005, J. Portfolio Management) [WP/PR]**
  - Rising VIX/VXN coincides with negative index returns, while **very high implied-volatility levels are followed by short-term gains**.
  - Average-to-moderately-high VIX levels led to unfavorable mean-variance outcomes.
  - The published 2005 version was not directly verified. — [UCLouvain repository (Giot WP)](https://research.dial.uclouvain.be/bitstreams/995d05e0-4d85-40d7-8ea7-3391a0e4365e/download)
  - Contradiction: a 1993–2018 S&P 500 study **does not support** the Giot (2005) / Banerjee–Doran–Peterson (2007) result that low VIX levels lead to negative returns. — [Vaasa thesis [THESIS]](https://osuva.uwasa.fi/items/6015ca72-cbac-4daf-a331-c9ce139205d0/full)
- **Johnson (working paper, U. Houston seminar) [WP]**: the **VIX level alone has little predictive power** for future S&P 500 returns, while the **VIX term structure** predicts next-quarter S&P 500 returns (adjusted R² 5.2%). — [Johnson WP PDF](https://www.bauer.uh.edu/departments/finance/documents/seminars/Johnson_020712.pdf)
- **Bollerslev, Tauchen & Zhou (2009, RFS) [PR]**
  - The variance risk premium (model-free implied variance minus realized variance) explains **over 15% of quarterly excess market-return variation (1990–2005)**, and over 25% together with P/E.
  - Later work criticizes the martingale assumption for conditional variance used to build the VRP. — [Fed FEDS 2007-11 WP version](https://www.federalreserve.gov/Pubs/Feds/2007/200711/200711pap.pdf); [SSRN](https://papers.ssrn.com/abstract=948309)
- An aggregator summary reports a positive relation between the current VIX and S&P 500 returns over the next 22 and 44 trading days. — [CXO Advisory [PRAC]](https://www.cxoadvisory.com/volatility-effects/measuring-investortrader-risk-aversion/)
- **Baltussen, Da, Lammers & Martens (2021, JFE 142(1):377–403) [PR]**, using 60+ futures over 1974–2020.
  - The return over the rest of the day positively predicts the last-30-minute return ("market intraday momentum"), and this **reverts over the following days**.
  - The cause is **gamma-hedging demand** from option market makers and leveraged ETFs: when dealers are short gamma, hedging trades go with the move. — [Erasmus Pure](https://pure.eur.nl/en/publications/hedging-demand-and-market-intraday-momentum/); [author PDF](https://www3.nd.edu/~zda/intramom.pdf)
- **Li & Yu (2012, JFE 104:401–419) [PR]**: **nearness to the Dow 52-week high positively predicts** future market returns, while **nearness to the historical (all-time) high negatively predicts** them. The information is not captured by macro variables, and it holds across G7 countries. — [SUFE abstract record](https://academicnewsletter.sufe.edu.cn/info/356820); [UMN experts](https://experts.umn.edu/en/publications/investor-attention-psychological-anchors-and-stock-return-predict/fingerprints/)
- De Bondt–Thaler-style overreaction at index level: the index-specific tests above (Lasfer et al.; Dolvin & Pyles; Joseph & Mazouz) are mixed and era-dependent (see Q1).

### Inferences
- The literature supports a **conditional** reversal model. A large down move is more likely to be followed by a bounce when (a) volatility or VIX is high, (b) the decline came on heavy volume, and (c) the period is post-2000 (index-product era). It is less likely in low-volatility, low-volume and pre-2000 regimes.
- Li & Yu suggest the Monitor's "drawdown from 52-week high" has a **monotone-negative** relationship with forward returns over 1–12 months (deeper drawdown, lower expected return). That may cut against a naive "deeper dip, bigger bounce" story. Their horizon is monthly, not 1–20 days.
- The Baltussen et al. gamma result points to a measurable state variable, dealer gamma sign. When dealers are negative-gamma, selloffs can extend intraday, then mean-revert over days. FinoAgent already computes Net GEX and gamma-flip (DealerPositioningPanel), so a feature could reuse that; historical GEX would be needed for analogs.

### Gaps
- I found no peer-reviewed out-of-sample test of a "buy the dip after N-σ 3-day S&P decline" rule. Practitioner tests (Connors RSI(2) etc.) exist but were not verified here.
- Banerjee, Doran & Peterson (2007) could not be retrieved directly.
- No peer-reviewed short-horizon (1–20 day) evidence on **credit spreads** (HY OAS, excess bond premium) as a conditioner was found in this pass.

---

## Q5. Which conditioning variables have the strongest support for a nearest-neighbour analog engine, and what implementable upgrades follow?

### Takeaway
The strongest-supported conditioners for **short-horizon reversal vs continuation** are:
- **volatility regime** (realized volatility and the VIX level; Nagel, Sentana–Wadhwani, LeBaron);
- **volume on the decline day** (Campbell–Grossman–Wang);
- **era / index-product regime** (Baltussen et al.).

For **level and risk**, the strongest are:
- trend state (Neely et al.; Goulding–Harvey–Mazzoleni);
- 52-week-high nearness (Li & Yu);
- VRP / VIX term structure (Bollerslev–Tauchen–Zhou; Johnson).

The single most robust forecast is **higher forward volatility after declines** (Patton–Sheppard, GJR). Weak or fragile: VIX *level alone* as a return predictor, MA-rule profitability out-of-sample, and volatility-managed timing out-of-sample.

### Cited Findings
- Nearest-neighbour forecasting of indexes: Fernández-Rodríguez, Sosvilla-Rivero & García-Artiles (1999) applied NN local predictors to the Nikkei 225 over 1986–1997.
  - They did **worse than a random walk on Theil's U** but **beat it on direction**.
  - A filter strategy beat buy-and-hold, especially in **bear markets**. — [ULPGC repository record](https://accedacris.ulpgc.es/handle/10553/49193?locale=en)
- Perlin & Ceretta: multivariate NN forecasts reached directional accuracy "moderately above 50%", while the random walk won on numerical accuracy. — [UFRGS PDF](https://seer.ufrgs.br/index.php/read/article/download/39956/25466/157924)
- Andrada-Félix, Fernández-Rodríguez & Fuertes (2016): applied NN to **S&P 100 realized variance** and argued it avoids model-misspecification risk. — [City Research Online](https://openaccess.city.ac.uk/12654)
- Data-snooping risk in rule and feature selection is serious. Sullivan–Timmermann–White's out-of-sample collapse (Q2) and Cederburg et al.'s out-of-sample failure (Q3) both come from parameter or structural instability. — [STW (LSE)](https://researchonline.lse.ac.uk/id/eprint/119144); [Cederburg et al.](https://www.lehigh.edu/~xuy219/research/COWY.pdf)
- Baltussen et al.'s MAC(q) is a principled multi-day serial-dependence statistic: linearly declining weights on lags 1..q−1, mapping to the variance-ratio test. The EMAC variant uses exponential weights. — [Baltussen et al.](https://academicweb.nd.edu/~zda/Indexing.pdf)

### Inferences — concrete, implementable upgrades for the Selloff Monitor
Current design: kNN on 3-day σ move, last-day σ move, drawdown from 52-week high, distance to 200-day MA and log VIX; non-overlapping forward windows; Wilson CIs vs an all-days baseline.

1. **Era handling (highest priority; backed by Baltussen et al., Ahn et al.).**
   - Restrict analogs to post-1993 (SPY era) or post-1999, or add an era feature or weight.
   - At minimum, show a **pre-1999 vs post-1999 split** of analog outcomes. S&P 500 MAC(5) flipped from +0.050 to −0.141, with a CUSUM break in Aug 1987.
   - If pre-1990s data are kept, prefer futures-based or total-return series. Cash-index data before about 1990 carry stale-price positive autocorrelation.
2. **Add a volume-shock feature (Campbell–Grossman–Wang; Baltussen controls).**
   - Use log(volume / 50-day average) on the selloff days, for SPY or index volume.
   - Theory and evidence say high-volume declines are more likely to reverse.
3. **Add a realized-volatility regime feature distinct from VIX (Sentana–Wadhwani; LeBaron; Patton–Sheppard).**
   - Use 20-day realized downside semivariance, or the RV/60-day-vol ratio.
   - Optionally add the **VRP proxy (VIX²/252×21 − RV21)** and **VIX term structure (VIX/VIX3M)**. Johnson finds the term structure beats the VIX level, and Bollerslev–Tauchen–Zhou find VRP explains over 15% of quarterly variance.
   - Log VIX alone is the weakest-supported return conditioner of the current five.
4. **Make trend state categorical (Goulding–Harvey–Mazzoleni).**
   - Hard-partition or heavily penalize cross-state neighbours using Bull / Correction / Bear / Rebound, defined by the sign of the 12-month return and the 1-month return. Alternatively use the sign of distance to the 200-day MA.
   - A selloff in a Correction (uptrend) should not borrow analogs from Bear states just because the σ-moves match.
5. **Add an all-time-high nearness feature next to the 52-week-high drawdown (Li & Yu).** The two anchors predict in **opposite** directions, so a 10% drawdown at an all-time high is a different state from a 10% drawdown in a multi-year range.
6. **Use a MAC/EMAC-style multi-day move summary (Baltussen et al.).**
   - Replace or augment the raw 3-session σ move with a weighted sum of the last 5 daily z-scores, using linearly declining weights (4,3,2,1)/q, or with EMAC.
   - This matches the statistic that captures modern index reversal.
7. **Forecast the distribution, not just a win rate (GJR, HAR, Patton–Sheppard, Moreira–Muir).** For each horizon, report:
   - median and mean forward return;
   - 5th/95th percentiles;
   - **forward realized volatility vs baseline**;
   - P(further −k·σ before +k·σ), which ties to the existing firstPassage helper;
   - forward returns in **σ units** as well as %.
   Post-selloff dispersion is reliably higher. A win rate that matches baseline can hide a much fatter left tail.
8. **Use a regime-matched baseline, not only all days.** Compare analog outcomes with all days in the same VIX tercile and trend state. This separates the "selloff" effect from the volatility-premium effect: high VIX alone predicts higher forward returns in Giot and the CXO summary.
9. **Fix the effective sample size behind the Wilson CIs.**
   - Non-overlapping forward windows are good, but neighbours still **cluster by episode** (Oct 2008, Mar 2020, Aug 2015, etc.).
   - Wilson CIs assume i.i.d. Bernoulli draws, so precision is overstated.
   - De-duplicate to **one analog per episode** (for example, a minimum 20-trading-day gap), report **distinct episodes / distinct years**, and use an **episode-block bootstrap** for CIs.
10. **Walk-forward validation and frozen hyper-parameters (Sullivan–Timmermann–White; Cederburg et al.).**
    - Pick k, feature weights and distance metric (Mahalanobis or standardized with learned weights) **once**, using only data before a cutoff.
    - Report out-of-sample calibration of the analog win rate vs realized outcomes (Brier score, reliability by decile) on post-cutoff data.
    - Consider kernel weighting by distance instead of a hard top-k.
11. **Optional: dealer gamma sign (Baltussen, Da, Lammers & Martens 2021).**
    - If a historical GEX series can be built (or a proxy), negative-gamma states predict intraday continuation then multi-day reversion.
    - This is a direct mechanism for "selloff extends, then snaps back".
    - The app already computes Net GEX live, but history is the binding constraint.
12. **Horizon framing.**
    - Expect the reversal tendency to be strongest at **1–5 days** (MAC(5) horizon; Nagel's weekly reversal).
    - Expect the trend/TSMOM continuation tendency to dominate at **1–12 months** (Moskowitz–Ooi–Pedersen).
    - The 20-day horizon sits in between and should be labelled lower-confidence.

### Gaps
- I found no peer-reviewed study that ranks these specific five features (3-day σ, 1-day σ, 52-week drawdown, 200-day MA distance, log VIX) jointly in a kNN analog framework for the S&P 500. The ranking above is inferred from separate single-variable studies.
- Credit-spread conditioning (HY OAS, Gilchrist–Zakrajšek excess bond premium) at 1–20 day horizons is unverified. No source was retrieved in this pass.
- Mizrach (1992) multivariate nearest-neighbour forecasting could not be retrieved.
- Practitioner "buy the dip" statistics (Connors, Quantpedia, etc.) were not verified and should not be treated as evidence without replication.
